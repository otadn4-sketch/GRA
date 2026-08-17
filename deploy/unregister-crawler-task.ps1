[CmdletBinding()]
param(
    [string]$TaskName = "GarayeBaleCrawler"
)

$ErrorActionPreference = "Stop"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Crawler task $TaskName was not found."
    exit 0
}
if ($task.State -eq "Running") { Stop-ScheduledTask -TaskName $TaskName }
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Crawler task $TaskName was removed."
