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

# --- 后台执行器（worker） ------------------------------------------------------
# start.ps1 会把 worker 的 pid 写进 logs\worker.pid。它不占端口，按端口找不到，
# 必须单独收：否则服务停了，worker 还在后台调模型花钱。
$pidFile = Join-Path $root "logs\worker.pid"
if (Test-Path $pidFile) {
    $lines = Get-Content $pidFile -ErrorAction SilentlyContinue
    $workerId = ""
    $workerStarted = ""
    foreach ($line in $lines) {
        if ($line -match '^\s*pid\s*=\s*(\d+)\s*$') { $workerId = $Matches[1] }
        elseif ($line -match '^\s*started\s*=\s*(.+?)\s*$') { $workerStarted = $Matches[1] }
        elseif ($line -match '^\s*(\d+)\s*$') { $workerId = $Matches[1] }
    }
    $workerProc = if ($workerId) { Get-Process -Id ([int]$workerId) -ErrorAction SilentlyContinue } else { $null }
    if (-not $workerId) {
        Write-Warn "logs\worker.pid 里没有进程号，忽略"
    } elseif (-not $workerProc) {
        Write-Step "后台执行器（pid $workerId）已经不在运行"
    } else {
        # 光凭 pid 不够：Windows 会复用进程号。核对启动时间，对不上就当它已经换了主人。
        $ours = $true
        if ($workerStarted) {
            try {
                $recorded = [datetime]::Parse($workerStarted).ToUniversalTime()
                $actual = $workerProc.StartTime.ToUniversalTime()
                if ([math]::Abs(($actual - $recorded).TotalSeconds) -gt 120) { $ours = $false }
            } catch {
                # 读不到启动时间就退回看命令行，两边都读不到才放过它
                $cmd = Get-CommandLine ([int]$workerId)
                if ($cmd -and $cmd -notmatch "run_worker") { $ours = $false }
            }
        }
        if (-not $ours -and -not $Force) {
            Write-Warn ("logs\worker.pid 指向 pid {0}，但它不像是这次启动的执行器，跳过（确认要停加 -Force）。" -f $workerId)
        } elseif ($WhatIf) {
            Write-Step "-WhatIf：会停止后台执行器 pid $workerId"
        } else {
            Stop-Process -Id ([int]$workerId) -Force -ErrorAction SilentlyContinue
            Write-Step "后台执行器已停止（pid $workerId）"
        }
    }
    if (-not $WhatIf) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
}

if ($stopped -eq 0 -and $refused -eq 0) {
    Write-Step "没有需要停止的进程。"
}
exit 0
