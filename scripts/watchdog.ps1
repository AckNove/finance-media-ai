# 守护启动 finance-media-ai（Windows / PowerShell）
#
# 和 start.ps1 的区别：
#   start.ps1  前台跑一次，Ctrl+C 就结束（日常开发用）
#   watchdog.ps1 常驻守护：进程崩了自动拉起，带退避与重启上限，日志落 logs\watchdog-*.log
#
# 设计原则：
#   1. 不碰 data/finance_media.db —— 默认工作库 data/finance_media.work.db。
#   2. 崩溃要留下证据：每轮启动/退出码/健康检查结果都写日志，重启次数超上限就停手等人工看。
#   3. 退避：连续崩溃时 3s → 6s → 12s → 24s（封顶 60s），避免疯狂重启刷日志。
#   4. 健康检查：进程活着但 /health 打不通，同样算失败（僵死进程比崩溃更难发现）。
#
# 注意：本文件必须保存成 **UTF-8 带 BOM**（原因见 start.ps1 顶部注释）。

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1",
    [string]$Database = "",
    [int]$MaxRestarts = 5,
    [int]$HealthTimeoutSeconds = 20,
    [string]$LogDir = "logs"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
# 子进程（uvicorn/自检）输出中文，控制台要按 UTF-8 解码，否则日志里全是乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$logDirPath = if ([System.IO.Path]::IsPathRooted($LogDir)) { $LogDir } else { Join-Path $root $LogDir }
New-Item -ItemType Directory -Force -Path $logDirPath | Out-Null
$logFile = Join-Path $logDirPath ("watchdog-{0}.log" -f (Get-Date -Format "yyyyMMdd"))

function Write-Log([string]$Level, [string]$Message) {
    $line = "[{0}] [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Log "ERROR" "没有 .venv：先跑 .\scripts\start.ps1 让它创建并装依赖；这里不替你装。"
    exit 1
}

if ([string]::IsNullOrWhiteSpace($Database)) { $Database = "data/finance_media.work.db" }
$dbPath = if ([System.IO.Path]::IsPathRooted($Database)) { $Database } else { Join-Path $root $Database }
$dbUrl = "sqlite:///" + ($dbPath -replace "\\", "/")

$protectedName = "finance_media.db"
if ((Split-Path -Leaf $dbPath) -eq $protectedName) {
    Write-Log "ERROR" "拒绝在 $protectedName 上跑守护：那是只读的开发库。用 -Database data/other.db。"
    exit 2
}

$env:DATABASE_URL = $dbUrl
$env:PYTHONIOENCODING = "utf-8"

function Test-Health([int]$TargetPort) {
    try {
        $response = Invoke-WebRequest -Uri "http://${BindHost}:${TargetPort}/health" -UseBasicParsing -TimeoutSec 3
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

Write-Log "INFO" ("守护启动：{0}:{1} ｜ 库 {2} ｜ 最多重启 {3} 次 ｜ 日志 {4}" -f $BindHost, $Port, $dbPath, $MaxRestarts, $logFile)

$restarts = 0
$delay = 3

while ($true) {
    $startedAt = Get-Date
    Write-Log "INFO" ("启动 uvicorn（第 {0} 次）" -f ($restarts + 1))

    $arguments = @("-m", "uvicorn", "backend.app.main:app", "--host", $BindHost, "--port", "$Port")
    $process = Start-Process -FilePath $python -ArgumentList $arguments -NoNewWindow -PassThru

    # 等健康检查（进程可能秒退，也可能起得很慢）
    $healthy = $false
    $deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 700
        if ($process.HasExited) { break }
        if (Test-Health $Port) { $healthy = $true; break }
    }

    if ($healthy) {
        Write-Log "INFO" ("健康检查通过：http://{0}:{1}/health" -f $BindHost, $Port)
        $restarts = 0
        $delay = 3
    } elseif (-not $process.HasExited) {
        Write-Log "WARN" ("{0} 秒内 /health 没通，按失败处理并结束进程" -f $HealthTimeoutSeconds)
        try { Stop-Process -Id $process.Id -Force } catch { }
    }

    $process.WaitForExit()
    $exitCode = $process.ExitCode
    $uptime = [int]((Get-Date) - $startedAt).TotalSeconds
    Write-Log "WARN" ("进程退出：code={0} ｜ 存活 {1} 秒" -f $exitCode, $uptime)

    $restarts++
    if ($restarts -gt $MaxRestarts) {
        Write-Log "ERROR" ("连续失败 {0} 次，停止守护。先看上面的退出码和 logs，再决定要不要重来。" -f $restarts)
        exit 1
    }

    Write-Log "INFO" ("{0} 秒后重启（第 {1}/{2} 次）" -f $delay, $restarts, $MaxRestarts)
    Start-Sleep -Seconds $delay
    $delay = [Math]::Min($delay * 2, 60)
}
