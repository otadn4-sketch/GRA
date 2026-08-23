# Run this script from an elevated PowerShell.
[CmdletBinding()]
param([string]$TaskName = "GarayeNewsletter")
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "windows\uninstall_backend_task.ps1") -TaskName $TaskName
