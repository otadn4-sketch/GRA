# Stop and remove the Windows Scheduled Task for the Garaye backend supervisor.
# Run from an elevated PowerShell. Compatible with Windows PowerShell 5.1 and PowerShell 7.

[CmdletBinding()]
param(
    [string]$TaskName = "GarayeNewsletter"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell (Run as administrator)."
}

$task = Get-ScheduledTaskSafe -TaskName $TaskName
if (-not $task) {
    Write-Host "Task $TaskName does not exist."
    exit 0
}

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
& (Join-Path $PSScriptRoot "stop_backend.ps1")
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-GarayeLog "Scheduled task $TaskName was removed."
Write-Host "Task $TaskName was removed."
