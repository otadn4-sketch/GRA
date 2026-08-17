[CmdletBinding()]
param([string]$BaseUrl = "http://127.0.0.1:8000")
$ErrorActionPreference = "Stop"
$health = Invoke-RestMethod -Method Get -Uri "$($BaseUrl.TrimEnd('/'))/health" -TimeoutSec 20
$health | ConvertTo-Json -Depth 8
if (-not $health.ok) { throw "Health check failed." }
