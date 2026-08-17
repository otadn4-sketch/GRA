[CmdletBinding()]
param(
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Run deploy\install.ps1 first."
}
Set-Location -LiteralPath $projectRoot
$env:PYTHONIOENCODING = "utf-8"
& $python -m uvicorn app.main:app --host $HostAddress --port $Port --proxy-headers
