# 一键停止 finance-media-ai（Windows / PowerShell）
#
# 设计原则：
#   1. 只停"我们自己的"进程：按端口找到监听者，再核对命令行里含 uvicorn 与本仓库路径；
#      不认识的进程默认不动，除非你加 -Force。
#   2. 先礼后兵：先 Ctrl+C 等价（CloseMainWindow 不适用控制台，故走 Stop-Process 前先给 3 秒），
#      3 秒还在就直接 Stop-Process。
#   3. 不碰数据库：本脚本不迁移、不删库、不动 data/finance_media.db。
#
# 注意：本文件必须保存成 **UTF-8 带 BOM**（原因见 start.ps1 顶部注释）。

[CmdletBinding()]
param(
    [int[]]$Port = @(8000),
    [switch]$Force,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Write-Step($message) { Write-Host "[stop] $message" -ForegroundColor Cyan }
function Write-Warn($message) { Write-Host "[stop] $message" -ForegroundColor Yellow }

function Get-Listeners([int]$TargetPort) {
    $connections = Get-NetTCPConnection -LocalPort $TargetPort -State Listen -ErrorAction SilentlyContinue
    if (-not $connections) { return @() }
    return $connections | Select-Object -ExpandProperty OwningProcess -Unique
}

function Get-CommandLine([int]$ProcessId) {
    try {
        return (Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop).CommandLine
    } catch {
        return ""
    }
}

$stopped = 0
$refused = 0

foreach ($current in $Port) {
    $owners = Get-Listeners $current
    if (-not $owners -or $owners.Count -eq 0) {
        Write-Step "端口 $current 上没有监听进程（可能本来就没在跑）"
        continue
    }

    foreach ($processId in $owners) {
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if (-not $proc) {
            Write-Warn "端口 $current 的占用进程 $processId 已经不存在"
            continue
        }
        $commandLine = Get-CommandLine $processId
        $looksLikeOurs = ($commandLine -match "uvicorn") -and ($commandLine -match "backend\.app\.main")
        if (-not $looksLikeOurs -and -not $Force) {
            Write-Warn ("端口 $current 被 {0} (pid {1}) 占用，但看起来不是本项目的服务，跳过。" -f $proc.ProcessName, $processId)
            Write-Warn "确定要停它：.\scripts\stop.ps1 -Port $current -Force"
            $refused++
            continue
        }
        if ($WhatIf) {
            Write-Step ("-WhatIf：会停止 {0} (pid {1})" -f $proc.ProcessName, $processId)
            continue
        }

        Write-Step ("正在停止 {0} (pid {1})" -f $proc.ProcessName, $processId)
        try {
            $proc.CloseMainWindow() | Out-Null
        } catch {
            # 控制台进程没有主窗口，忽略
        }
        if (-not $proc.WaitForExit(3000)) {
            Write-Warn "3 秒内没有正常退出，强制结束"
            Stop-Process -Id $processId -Force
            $proc.WaitForExit(3000) | Out-Null
        }
        if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
            Write-Warn "pid $processId 仍然存在，请手动检查"
        } else {
            $stopped++
            Write-Step ("端口 $current 已释放")
        }
    }
}

Write-Step "已停止 $stopped 个服务进程；跳过 $refused 个非本项目进程。"
if ($stopped -eq 0 -and $refused -eq 0) {
    Write-Step "没有需要停止的进程。"
}
exit 0
