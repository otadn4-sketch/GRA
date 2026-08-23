# Start the Garaye backend supervisor (Scheduled Task if registered, otherwise a hidden process).
# Compatible with Windows PowerShell 5.1 and PowerShell 7.

[CmdletBinding()]
param(
    [string]$TaskName = "GarayeNewsletter",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

Initialize-GarayeFolders
$python = Get-GarayePython
$healthUri = Get-GarayeHealthUri -HostAddress $HostAddress -Port $Port

if (Test-GarayeHealthOk -HealthUri $healthUri -TimeoutSec 10) {
    Write-Host "Backend is already healthy at $healthUri"
    exit 0
}

$task = Get-ScheduledTaskSafe -TaskName $TaskName
if ($task) {
    Write-Host "Starting scheduled task $TaskName ..."
    Start-ScheduledTask -TaskName $TaskName
}
else {
    $supervisorPid = Get-RecordedPid -Path $script:SupervisorPidPath
    if ($supervisorPid -and (Get-Process -Id $supervisorPid -ErrorAction SilentlyContinue)) {
        Write-Host "Supervisor is already running with PID $supervisorPid. Waiting for health..."
    }
    else {
        $powerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
        $supervisor = Join-Path $PSScriptRoot "backend_supervisor.ps1"
        $argument = "-NoProfile -ExecutionPolicy Bypass -File `"$supervisor`" -HostAddress $HostAddress -Port $Port"
        Start-Process -FilePath $powerShell -ArgumentList $argument `
            -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden | Out-Null
        Write-GarayeLog "Started hidden supervisor for ${HostAddress}:$Port"
    }
}

$ok = $false
for ($attempt = 1; $attempt -le 45; $attempt++) {
    Start-Sleep -Seconds 1
    if (Test-GarayeHealthOk -HealthUri $healthUri -TimeoutSec 10) {
        $ok = $true
        break
    }
}
if (-not $ok) {
    $tail = ""
    if (Test-Path -LiteralPath $script:SupervisorLog) {
        $tail = (Get-Content -LiteralPath $script:SupervisorLog -Tail 30) -join [Environment]::NewLine
    }
    throw "Backend did not become healthy at $healthUri.`n$tail"
}

Write-Host "Backend is healthy at $healthUri"
Write-Host "Uvicorn is bound to ${HostAddress}:$Port (not published to the Internet)."
