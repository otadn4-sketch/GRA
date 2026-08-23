[CmdletBinding()]
param([string]$BaseUrl = "http://127.0.0.1:8000")
$ErrorActionPreference = "Stop"
$uri = "$($BaseUrl.TrimEnd('/'))/health"
$health = Invoke-RestMethod -Method Get -Uri $uri -TimeoutSec 10
$health | ConvertTo-Json -Depth 8
if (-not ($health.status -eq "ok" -or $health.ok)) {
    throw "Health check failed."
}
