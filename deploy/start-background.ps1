[CmdletBinding()]
param(
    [string]$HostAddress = "0.0.0.0",
    [int]$Port = 8000,
    [switch]$Supervised
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$runDir = Join-Path $projectRoot "run"
$logDir = Join-Path $projectRoot "data\logs"
$pidPath = Join-Path $runDir "garaye.pid"
$supervisorPidPath = Join-Path $runDir "garaye-supervisor.pid"
$reloadFlag = Join-Path $runDir "reload.request"
$stopFlag = Join-Path $runDir "stop.request"
$pipFlag = Join-Path $runDir "pip.request"
$powerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path -LiteralPath $python)) { throw "Run deploy\install.ps1 first." }
foreach ($folder in @($runDir, $logDir)) {
    if (-not (Test-Path -LiteralPath $folder)) { New-Item -ItemType Directory -Path $folder | Out-Null }
}

$probeHost = if ($HostAddress -in @("0.0.0.0", "::", "[::]")) { "127.0.0.1" } else { $HostAddress }
$healthUri = "http://$($probeHost):$Port/health"

function Test-GarayeHealth {
    try {
        $health = Invoke-RestMethod -Method Get -Uri $healthUri -TimeoutSec 2
        if ($health.ok) { return $health }
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

if (-not $Supervised) {
    $existingSupervisor = Get-RecordedProcess $supervisorPidPath
    $existingService = Get-RecordedProcess $pidPath
    $health = Test-GarayeHealth
    if ($existingSupervisor -or ($existingService -and $health)) {
        if ($health) {
            Write-Host "Garaye v$($health.version) is already healthy on port $Port."
            Write-Host "Open http://$($probeHost):$Port/login"
            return
        }
        throw "Garaye is already running. Stop it with deploy\stop.ps1 first."
    }
    $argument = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Supervised -HostAddress $HostAddress -Port $Port"
    Start-Process -FilePath $powerShell -ArgumentList $argument `
        -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
    $health = $null
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        Start-Sleep -Milliseconds 750
        $health = Test-GarayeHealth
        if ($health) { break }
    }
    if (-not $health) {
        $errorLog = Join-Path $logDir "service.err.log"
        $tail = if (Test-Path -LiteralPath $errorLog) {
            (Get-Content -LiteralPath $errorLog -Tail 40 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
        } else {
            "service.err.log was not created."
        }
        throw "Garaye did not become healthy at $healthUri. Last service errors:`n$tail"
    }
    Write-Host "Garaye v$($health.version) is healthy on port $Port."
    Write-Host "Open http://$($probeHost):$Port/login"
    Write-Host "Live-update supervisor is running in the background."
    return
}

$existingSupervisor = Get-RecordedProcess $supervisorPidPath
if ($existingSupervisor -and $existingSupervisor.Id -ne $PID) {
    throw "Garaye supervisor is already running with PID $($existingSupervisor.Id)."
}
Set-Content -LiteralPath $supervisorPidPath -Value $PID -Encoding ASCII
if (Test-Path -LiteralPath $stopFlag) { Remove-Item -LiteralPath $stopFlag -Force }

function Install-UpdatedRequirements {
    if (-not (Test-Path -LiteralPath $pipFlag)) { return }
    Write-Host "Installing updated Python requirements before restart..."
    $env:PYTHONIOENCODING = "utf-8"
    & $python -m pip install --disable-pip-version-check --no-color -r (Join-Path $projectRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "pip install after live update failed; retrying on the next restart."
        return
    }
    Remove-Item -LiteralPath $pipFlag -Force -ErrorAction SilentlyContinue
}

function Start-GarayeChild {
    $outLog = Join-Path $logDir "service.out.log"
    $errLog = Join-Path $logDir "service.err.log"
    foreach ($logFileName in @("service.out.log", "service.err.log")) {
        $logFile = Join-Path $logDir $logFileName
        if (Test-Path -LiteralPath $logFile) {
            try {
                Remove-Item -LiteralPath $logFile -Force -ErrorAction Stop
            }
            catch {
                $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
                if ($logFileName -eq "service.out.log") {
                    $outLog = Join-Path $logDir "service.out.$stamp.log"
                }
                else {
                    $errLog = Join-Path $logDir "service.err.$stamp.log"
                }
            }
        }
    }
    $arguments = @(
        "-m", "uvicorn", "app.main:app",
        "--host", $HostAddress,
        "--port", [string]$Port,
        "--proxy-headers"
    )
    $process = Start-Process -FilePath $python -ArgumentList $arguments `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog
    Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ASCII
    return $process
}

try {
    $first = $true
    while (-not (Test-Path -LiteralPath $stopFlag)) {
        if (Test-Path -LiteralPath $reloadFlag) {
            Remove-Item -LiteralPath $reloadFlag -Force -ErrorAction SilentlyContinue
        }
        Install-UpdatedRequirements
        $leftover = Get-RecordedProcess $pidPath
        if ($leftover) {
            if ($first) { throw "Garaye is already running with PID $($leftover.Id)." }
            Stop-Process -Id $leftover.Id -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        }
        $process = Start-GarayeChild
        $health = $null
        for ($attempt = 1; $attempt -le 20; $attempt++) {
            Start-Sleep -Milliseconds 750
            if ($process.HasExited) { break }
            $health = Test-GarayeHealth
            if ($health) { break }
        }
        if (-not $health -or -not $health.ok) {
            if (-not $process.HasExited) {
                Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
            }
            if ($first) {
                $errorLog = Join-Path $logDir "service.err.log"
                $tail = if (Test-Path -LiteralPath $errorLog) {
                    (Get-Content -LiteralPath $errorLog -Tail 40 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
                } else {
                    "service.err.log was not created."
                }
                throw "Garaye did not become healthy at $healthUri. Last service errors:`n$tail"
            }
            Write-Warning "Garaye failed to become healthy after a live-update restart. Retrying in 3 seconds."
            Start-Sleep -Seconds 3
            continue
        }
        if ($first) {
            Write-Host "Garaye v$($health.version) is healthy with PID $($process.Id) on port $Port."
            Write-Host "Supervisor is watching run\reload.request for live updates."
        }
        $first = $false
        Wait-Process -Id $process.Id
        if (Test-Path -LiteralPath $stopFlag) { break }
        Start-Sleep -Seconds 1
    }
}
finally {
    if (Test-Path -LiteralPath $supervisorPidPath) {
        $recorded = (Get-Content -LiteralPath $supervisorPidPath -Raw).Trim()
        if ($recorded -eq [string]$PID) {
            Remove-Item -LiteralPath $supervisorPidPath -Force -ErrorAction SilentlyContinue
        }
    }
    if (Test-Path -LiteralPath $stopFlag) {
        Remove-Item -LiteralPath $stopFlag -Force -ErrorAction SilentlyContinue
    }
}
