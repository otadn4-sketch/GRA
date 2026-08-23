# Show backend, supervisor, port, health, and scheduled-task status.
# Compatible with Windows PowerShell 5.1 and PowerShell 7.

[CmdletBinding()]
param(
    [string]$TaskName = "GarayeNewsletter",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "_common.ps1")

$healthUri = Get-GarayeHealthUri -HostAddress $HostAddress -Port $Port
$uvicornPid = Get-RecordedPid -Path $script:PidPath
$supervisorPid = Get-RecordedPid -Path $script:SupervisorPidPath
$uvicornAlive = $false
$supervisorAlive = $false
if ($uvicornPid) {
    $uvicornAlive = [bool](Get-Process -Id $uvicornPid -ErrorAction SilentlyContinue)
}
if ($supervisorPid) {
    $supervisorAlive = [bool](Get-Process -Id $supervisorPid -ErrorAction SilentlyContinue)
}

$listeners = ConvertTo-GarayePidArray (Get-ListeningPids -Port $Port)
$listenText = if ($listeners.Count -gt 0) { ($listeners -join ", ") } else { "(not LISTENING)" }
$healthOk = Test-GarayeHealthOk -HealthUri $healthUri -TimeoutSec 10

$task = Get-ScheduledTaskSafe -TaskName $TaskName
$taskState = "(not registered)"
if ($task) { $taskState = [string]$task.State }

Write-Host "ProjectRoot     : $($script:ProjectRoot)"
Write-Host "Python          : $($script:PythonExe)"
Write-Host "Bind            : ${HostAddress}:$Port"
Write-Host "Health URI      : $healthUri"
Write-Host "Health          : $(if ($healthOk) { 'OK (HTTP 200, status=ok)' } else { 'FAIL' })"
Write-Host "Port $Port Pids   : $listenText"
Write-Host "Uvicorn PID     : $(if ($uvicornPid) { $uvicornPid } else { '(none)' }) alive=$uvicornAlive ourUvicorn=$(if ($uvicornPid) { Test-GarayeOurUvicorn -ProcessId $uvicornPid -Port $Port } else { $false })"
Write-Host "Supervisor PID  : $(if ($supervisorPid) { $supervisorPid } else { '(none)' }) alive=$supervisorAlive"
Write-Host "Scheduled Task  : $TaskName state=$taskState"
Write-Host "Supervisor log  : $($script:SupervisorLog)"
Write-Host "Uvicorn logs    : $($script:UvicornOutLog) | $($script:UvicornErrLog)"
Write-Host "App log         : $(Join-Path $script:ProjectRoot 'data\logs\prasad.log')"

if ($listeners.Count -gt 0) {
    foreach ($listenerId in $listeners) {
        Write-Host ("Listener {0}: {1}" -f $listenerId, (Get-ProcessCommandLine -ProcessId $listenerId))
    }
}

if ($healthOk) { exit 0 }
exit 1
