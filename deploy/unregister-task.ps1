# Run this script from an elevated PowerShell.
[CmdletBinding()]
param([string]$TaskName = "GarayeNewsletter")
$ErrorActionPreference = "Stop"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Task does not exist."
    exit 0
}
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Task $TaskName was removed."
