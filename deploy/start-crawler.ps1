[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$scriptPath = Join-Path $projectRoot "crawler\bale_crawler_api_sender.py"
$runDir = Join-Path $projectRoot "run"
$logDir = Join-Path $projectRoot "data\logs"
$pidPath = Join-Path $runDir "crawler.pid"
if (-not (Test-Path -LiteralPath $python)) { throw "Run deploy\install.ps1 first." }
foreach ($folder in @($runDir, $logDir)) {
    if (-not (Test-Path -LiteralPath $folder)) {
        New-Item -ItemType Directory -Path $folder | Out-Null
    }
}
if (Test-Path -LiteralPath $pidPath) {
    $oldPid = [int](Get-Content -LiteralPath $pidPath -Raw)
    if (Get-Process -Id $oldPid -ErrorAction SilentlyContinue) {
        throw "Bale crawler is already running with PID $oldPid."
    }
}

$enabled = $false
$envPath = Join-Path $projectRoot ".env"
if (Test-Path -LiteralPath $envPath) {
    foreach ($line in Get-Content -LiteralPath $envPath) {
        if ($line -match '^\s*BALE_CRAWLER_ENABLED\s*=\s*(.+?)\s*$') {
            $value = $matches[1].Trim().Trim('"').Trim("'").ToLowerInvariant()
            $enabled = $value -in @("1", "true", "yes", "on")
        }
    }
}
# The dashboard's runtime switch is checked by the Python crawler before each
# cycle, so this launcher must start even when the initial .env value is off.

$process = Start-Process -FilePath $python `
    -ArgumentList @($scriptPath) -WorkingDirectory $projectRoot `
    -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $logDir "crawler.out.log") `
    -RedirectStandardError (Join-Path $logDir "crawler.err.log")
Start-Sleep -Seconds 2
if ($process.HasExited) {
    throw "Crawler exited during startup. Check data\logs\crawler.err.log."
}
Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ASCII
Write-Host "Bale crawler started with PID $($process.Id)."
