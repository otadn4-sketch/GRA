# Task Scheduler entrypoint. Logs crashes that happen before backend_supervisor.ps1 can write its own log.
# Compatible with Windows PowerShell 5.1 and PowerShell 7.

[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$windowsDir = $PSScriptRoot
$projectRoot = (Resolve-Path (Join-Path $windowsDir "..\..")).Path
$logDir = Join-Path $projectRoot "logs"
$crashLog = Join-Path $logDir "supervisor-crash.log"
if (-not (Test-Path -LiteralPath $logDir)) {
    New-Item -ItemType Directory -Path $logDir | Out-Null
}

try {
    Set-Location -LiteralPath $projectRoot
    & (Join-Path $windowsDir "backend_supervisor.ps1") -HostAddress $HostAddress -Port $Port
}
catch {
    $line = "{0} [ERROR] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_.Exception.Message
    $encoding = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::AppendAllText($crashLog, $line + [Environment]::NewLine, $encoding)
    throw
}
