# Register or update the Windows Scheduled Task that runs the backend supervisor at startup.
# Run from an elevated PowerShell. Compatible with Windows PowerShell 5.1 and PowerShell 7.

[CmdletBinding()]
param(
    [string]$TaskName = "GarayeNewsletter",
    [int]$Port = 8000,
    [string]$HostAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

Initialize-GarayeFolders
Grant-GarayeSystemAccess
$python = Get-GarayePython
$supervisor = Join-Path $PSScriptRoot "run_supervisor.ps1"
$powerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $powerShell)) {
    throw "Windows PowerShell 5.1 was not found at $powerShell"
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell (Run as administrator)."
}

$existing = Get-ScheduledTaskSafe -TaskName $TaskName
$updated = [bool]$existing
if ($existing) {
    Write-Host "Updating existing scheduled task $TaskName (no duplicate will be created)."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

$argument = "-NoProfile -ExecutionPolicy Bypass -File `"$supervisor`" -HostAddress $HostAddress -Port $Port"
$action = New-ScheduledTaskAction -Execute $powerShell -Argument $argument -WorkingDirectory $script:ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
try {
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -DontStopOnIdleEnd `
        -StartWhenAvailable `
        -RestartCount 5 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit ([TimeSpan]::Zero)
}
catch {
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 5 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Days 3650)
}
$taskPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $taskPrincipal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

$mode = "created"
if ($updated) { $mode = "updated" }
Write-GarayeLog "Scheduled task $TaskName $mode. ProjectRoot=$($script:ProjectRoot) python=$python host=$HostAddress port=$Port"
Write-Host "Task $TaskName was $mode and started as SYSTEM at Windows startup."
Write-Host "Working directory: $($script:ProjectRoot)"
Write-Host "Supervisor: $supervisor"
Write-Host "Uvicorn bind: ${HostAddress}:$Port"
