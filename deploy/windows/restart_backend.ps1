# Restart the Garaye backend supervisor and uvicorn.
# Compatible with Windows PowerShell 5.1 and PowerShell 7.

[CmdletBinding()]
param(
    [string]$TaskName = "GarayeNewsletter",
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "stop_backend.ps1") -TaskName $TaskName -Port $Port
Start-Sleep -Seconds 3
& (Join-Path $PSScriptRoot "start_backend.ps1") -TaskName $TaskName -HostAddress $HostAddress -Port $Port
