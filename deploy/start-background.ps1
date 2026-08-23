[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [switch]$Supervised
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runDir = Join-Path $projectRoot "run"
$logDir = Join-Path $projectRoot "logs"
$supervisorPidPath = Join-Path $runDir "garaye-supervisor.pid"
$powerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$supervisorScript = Join-Path $PSScriptRoot "windows\backend_supervisor.ps1"

if (-not (Test-Path -LiteralPath $python)) { throw "Run deploy\install.ps1 first." }
foreach ($folder in @($runDir, $logDir)) {
    if (-not (Test-Path -LiteralPath $folder)) { New-Item -ItemType Directory -Path $folder | Out-Null }
}

if ($HostAddress -in @("0.0.0.0", "::", "[::]")) {
    $HostAddress = "127.0.0.1"
}

$healthUri = "http://127.0.0.1:$Port/health"

function Test-GarayeHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Method Get -Uri $healthUri -TimeoutSec 10
        if ([int]$response.StatusCode -ne 200) { return $null }
        $health = $response.Content | ConvertFrom-Json
        if ($health.status -eq "ok" -or $health.ok) { return $health }
    }
    catch {
        return $null
    }
    return $null
}

function Get-RecordedProcess([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $raw = (Get-Content -LiteralPath $Path -Raw).Trim()
    if (-not $raw) { return $null }
    $recordedPid = 0
    if (-not [int]::TryParse($raw, [ref]$recordedPid)) { return $null }
    return Get-Process -Id $recordedPid -ErrorAction SilentlyContinue
}

if ($Supervised) {
    & $supervisorScript -HostAddress $HostAddress -Port $Port
    exit $LASTEXITCODE
}

$existingSupervisor = Get-RecordedProcess $supervisorPidPath
$health = Test-GarayeHealth
if ($existingSupervisor -or $health) {
    if ($health) {
        $version = $health.version
        Write-Host "Garaye v$version is already healthy on 127.0.0.1:$Port."
        Write-Host "Open http://127.0.0.1:$Port/login"
        return
    }
    throw "Garaye supervisor is already running. Stop it with deploy\windows\stop_backend.ps1 first."
}

$argument = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Supervised -HostAddress $HostAddress -Port $Port"
Start-Process -FilePath $powerShell -ArgumentList $argument `
    -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
$health = $null
for ($attempt = 1; $attempt -le 45; $attempt++) {
    Start-Sleep -Seconds 1
    $health = Test-GarayeHealth
    if ($health) { break }
}
if (-not $health) {
    $errorLog = Join-Path $logDir "backend-supervisor.log"
    $tail = if (Test-Path -LiteralPath $errorLog) {
        (Get-Content -LiteralPath $errorLog -Tail 40 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
    } else {
        "backend-supervisor.log was not created."
    }
    throw "Garaye did not become healthy at $healthUri. Last supervisor log:`n$tail"
}
Write-Host "Garaye v$($health.version) is healthy on 127.0.0.1:$Port."
Write-Host "Open http://127.0.0.1:$Port/login"
Write-Host "Production supervisor is running in the background."
