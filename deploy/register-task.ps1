# Run this script from an elevated PowerShell.
[CmdletBinding()]
param(
    [string]$TaskName = "GarayeNewsletter",
    [int]$Port = 8000,
    [string]$HostAddress = "127.0.0.1"
)
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "windows\install_backend_task.ps1") -TaskName $TaskName -Port $Port -HostAddress $HostAddress
