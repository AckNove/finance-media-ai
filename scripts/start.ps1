# 一键启动 finance-media-ai（Windows / PowerShell）
#
# 设计原则：
#   1. 绝不碰 data/finance_media.db —— 那是只读的开发库，迁移与 WAL 一律不在它上面做。
#      默认使用独立的工作库 data/finance_media.work.db，第一次启动时自动建表（alembic upgrade head）。
#   2. 缺少依赖就明确报错并给出安装命令，不假装成功。
#   3. 启动后打印真实地址；用 -NoBrowser 可以不自动开浏览器（CI / 脚本里用）。

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$BindHost = "127.0.0.1",
    [string]$Database = "",
    [switch]$NoBrowser,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

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

# --- 2. 数据库 ---------------------------------------------------------------
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

# --- 3. 环境检查（缺什么就说什么） --------------------------------------------
$env:DATABASE_URL = $dbUrl
$env:PYTHONIOENCODING = "utf-8"
& $python -m backend.scripts.preflight
if ($LASTEXITCODE -ne 0) {
    Write-Warn "预检有阻塞项（见上）。服务仍会启动，但相关能力会是 BLOCKED 而不是伪造成功。"
}

# --- 4. 启动 ----------------------------------------------------------------
$url = "http://${BindHost}:${Port}"
Write-Step "工作台：$url/app/ ｜ 接口文档：$url/docs ｜ 健康检查：$url/api/system/status"

$arguments = @("-m", "uvicorn", "backend.app.main:app", "--host", $BindHost, "--port", "$Port")
if ($Reload) { $arguments += "--reload" }

if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($target)
        Start-Sleep -Seconds 3
        Start-Process $target
    } -ArgumentList "$url/app/" | Out-Null
}

& $python @arguments
exit $LASTEXITCODE
