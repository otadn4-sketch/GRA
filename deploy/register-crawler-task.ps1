# Run this script from an elevated PowerShell under the Windows account that is logged into Bale Web.
[CmdletBinding()]
param(
    [string]$TaskName = "GarayeBaleCrawler",
    [string]$UserName = "$env:USERDOMAIN\$env:USERNAME"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runScript = Join-Path $projectRoot "deploy\run-crawler.ps1"
$powerShell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$argument = "-NoProfile -ExecutionPolicy Bypass -File `"$runScript`""
$action = New-ScheduledTaskAction -Execute $powerShell -Argument $argument -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $UserName
$settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 2) -ExecutionTimeLimit (New-TimeSpan -Days 3650)
$principal = New-ScheduledTaskPrincipal -UserId $UserName -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Crawler task $TaskName was registered for $UserName and started."
