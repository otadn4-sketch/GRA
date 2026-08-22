[CmdletBinding()]
param(
    [switch]$Once,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$scriptPath = Join-Path $projectRoot "crawler\bale_crawler_api_sender.py"
$envPath = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $python)) { throw "Run deploy\install.ps1 first." }
if (-not (Test-Path -LiteralPath $scriptPath)) { throw "Crawler script was not found." }
if (-not (Test-Path -LiteralPath $envPath)) { throw ".env was not found." }

$enabled = $false
foreach ($line in Get-Content -LiteralPath $envPath) {
    if ($line -match '^\s*BALE_CRAWLER_ENABLED\s*=\s*(.+?)\s*$') {
        $value = $matches[1].Trim().Trim('"').Trim("'").ToLowerInvariant()
        $enabled = $value -in @("1", "true", "yes", "on")
    }
}
# The dashboard's runtime switch is checked by the Python crawler before each
# cycle, so this launcher must start even when the initial .env value is off.

Set-Location -LiteralPath $projectRoot
$env:PYTHONIOENCODING = "utf-8"
$arguments = @("-m", "app.crawler_launcher")
if ($Once) { $arguments += "--once" }
& $python @arguments
exit $LASTEXITCODE
