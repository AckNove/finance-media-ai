# 一键启动 finance-media-ai（Windows / PowerShell）
#
# 设计原则：
#   1. 绝不碰 data/finance_media.db —— 那是只读的开发库，迁移与 WAL 一律不在它上面做。
#      默认使用独立的工作库 data/finance_media.work.db，第一次启动时自动建表（alembic upgrade head）。
#   2. 缺少依赖就明确报错并给出安装命令，不假装成功。
#   3. 启动后打印真实地址；用 -NoBrowser 可以不自动开浏览器（CI / 脚本里用）。
#   4. 端口被占用直接说清楚是谁占的，别把 uvicorn 的堆栈甩给人看。
#
# 注意：本文件必须保存成 **UTF-8 带 BOM**。Windows PowerShell 5.1 在没有 BOM 时
# 会用系统 ANSI 代码页（中文机器上是 GBK）解析脚本，里面的中文会变成乱码。

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1",
    [string]$Database = "",
    [switch]$NoBrowser,
    [switch]$Reload,
    [switch]$SkipPreflight,
    # 默认连后台执行器一起起：不跑 worker，界面上建的研究任务会一直停在"已创建"，
    # 每天第一次用的人都会以为系统坏了。不想让它自动推进就加 -NoWorker。
    [switch]$NoWorker
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$OutputEncoding = [System.Text.Encoding]::UTF8
# 子进程（uvicorn/预检）输出中文，控制台按 UTF-8 解码，否则满是乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Write-Step($message) { Write-Host "[start] $message" -ForegroundColor Cyan }
function Write-Warn($message) { Write-Host "[start] $message" -ForegroundColor Yellow }

# --- 1. 解释器 ---------------------------------------------------------------
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Warn "没有找到 .venv，先创建虚拟环境并安装依赖（只需一次）"
    python -m venv .venv
    & $python -m pip install --upgrade pip
    & $python -m pip install -r requirements.txt
}
Write-Step ("python: " + (& $python --version))

# --- 2. 端口 ----------------------------------------------------------------
function Test-PortBusy([string]$TargetHost, [int]$TargetPort) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect($TargetHost, $TargetPort, $null, $null)
        $done = $async.AsyncWaitHandle.WaitOne(400)
        $open = $done -and $client.Connected
        $client.Close()
        return $open
    } catch {
        return $false
    }
}

if (Test-PortBusy $BindHost $Port) {
    Write-Host "[start] 端口 $Port 已被占用：$BindHost`:$Port 上还有服务在跑。" -ForegroundColor Red
    $owners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($processId in $owners) {
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($proc) { Write-Host ("[start]   占用进程：{0} (pid {1})" -f $proc.ProcessName, $proc.Id) -ForegroundColor Red }
    }
    Write-Host "[start] 换一个端口：.\scripts\start.ps1 -Port 8011（或先把上面那个进程停掉）" -ForegroundColor Red
    exit 3
}

# --- 3. 数据库 ---------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($Database)) {
    $Database = "data/finance_media.work.db"
}
$dbPath = if ([System.IO.Path]::IsPathRooted($Database)) { $Database } else { Join-Path $root $Database }
$dbUrl = "sqlite:///" + ($dbPath -replace "\\", "/")

$protected = (Resolve-Path -LiteralPath (Join-Path $root "data")).Path + "\finance_media.db"
if ($dbPath -eq $protected) {
    Write-Host "[start] 拒绝在 data/finance_media.db 上启动：那个库是只读的开发库，迁移与写入都不允许。" -ForegroundColor Red
    Write-Host "[start] 用 -Database data/other.db 指定另一个库。" -ForegroundColor Red
    exit 2
}

if (-not (Test-Path $dbPath)) {
    Write-Step "工作库不存在，创建并按迁移建表：$dbPath"
    $env:DATABASE_URL = $dbUrl
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[start] 建表失败，见上方 alembic 输出。" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Step "使用已有工作库：$dbPath"
}

# --- 4. 环境检查（缺什么就说什么） --------------------------------------------
$env:DATABASE_URL = $dbUrl
$env:PYTHONIOENCODING = "utf-8"
if (-not $SkipPreflight) {
    & $python -m backend.scripts.preflight
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "预检有阻塞项（见上）。服务仍会启动，但相关能力会是 BLOCKED 而不是伪造成功。"
    }
}

# --- 5. 后台执行器 ------------------------------------------------------------
# 没有 worker，任务只会停在 created/ready；配了模型也不会有人去推进。
# 用独立进程而不是 Start-Job：Stop-Job 杀不掉它拉起的 python 子进程，
# 结果就是"服务停了、worker 还在后台花钱"。pid 写进 logs\worker.pid 供 stop.ps1 收尾。
$workerProcess = $null
if (-not $NoWorker) {
    $logDir = Join-Path $root "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $workerOut = Join-Path $logDir "worker.out.log"
    $workerErr = Join-Path $logDir "worker.err.log"
    $workerProcess = Start-Process -FilePath $python `
        -ArgumentList @("-m", "backend.scripts.run_worker", "--forever") `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $workerOut -RedirectStandardError $workerErr
    # 记下 pid 和启动时间：Windows 会复用进程号，stop.ps1 靠"启动时间对得上"才敢动手，
    # 也不能只靠命令行判断——实测 python 子进程的 CommandLine 偶尔读出来是空的。
    $startedAt = ""
    try { $startedAt = (Get-Process -Id $workerProcess.Id).StartTime.ToUniversalTime().ToString("o") } catch { }
    Set-Content -Path (Join-Path $logDir "worker.pid") `
        -Value @("pid=$($workerProcess.Id)", "started=$startedAt") -Encoding ascii
    Write-Step "后台执行器已启动（pid $($workerProcess.Id)）：会自动推进你建的研究任务，"
    Write-Step "  花钱仍受预算闸门约束；日志 logs\worker.out.log；不想自动跑就加 -NoWorker"
} else {
    Write-Warn "按 -NoWorker 跳过后台执行器：任务会停在原地，需要手动跑 python -m backend.scripts.run_worker"
}

# --- 6. 启动 ----------------------------------------------------------------
$url = "http://${BindHost}:${Port}"
Write-Step "工作台：$url/app/ ｜ 接口文档：$url/docs ｜ 健康检查：$url/api/system/status"

$arguments = @("-m", "uvicorn", "backend.app.main:app", "--host", $BindHost, "--port", "$Port")
if ($Reload) { $arguments += "--reload" }

# 起来之前就先把"到底起没起来"告诉人：等 4 秒打一次真实健康检查。
$probe = Start-Job -ScriptBlock {
    param($target)
    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Milliseconds 800
        try {
            $response = Invoke-WebRequest -Uri "$target/health" -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                Write-Host "[start] 健康检查通过：$target/health -> 200" -ForegroundColor Green
                Write-Host "[start] 工作台地址：$target/app/" -ForegroundColor Green
                return
            }
        } catch {
            # 还没起来，继续等
        }
    }
    Write-Host "[start] 8 秒内没等到 /health 通过，往上看 uvicorn 输出里的报错。" -ForegroundColor Yellow
} -ArgumentList $url

if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($target)
        Start-Sleep -Seconds 4
        Start-Process $target
    } -ArgumentList "$url/app/" | Out-Null
}

try {
    & $python @arguments
} finally {
    if ($probe) { Remove-Job -Job $probe -Force -ErrorAction SilentlyContinue }
    if ($workerProcess -and -not $workerProcess.HasExited) {
        # 前台服务退出（Ctrl+C）时把 worker 一起收掉，别留在后台继续花钱。
        Stop-Process -Id $workerProcess.Id -Force -ErrorAction SilentlyContinue
        Write-Step "后台执行器已停止（pid $($workerProcess.Id)）"
    }
    $pidFile = Join-Path $root "logs\worker.pid"
    if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
    Write-Step "服务已退出。"
}
exit $LASTEXITCODE
