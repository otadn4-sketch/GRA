[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ZipPath
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) { throw "Run deploy\install.ps1 first." }
$env:GARAYE_UPDATE_ZIP = (Resolve-Path -LiteralPath $ZipPath).Path
Set-Location -LiteralPath $projectRoot
$env:PYTHONIOENCODING = "utf-8"
& $python -c @"
import json, os
from pathlib import Path
from app.live_update import apply_update_zip, request_reload
result = apply_update_zip(Path(os.environ['GARAYE_UPDATE_ZIP']).read_bytes(), actor='apply-update.ps1')
print(json.dumps(result, ensure_ascii=False, indent=2))
if result.get('python_restart_requested'):
    request_reload()
"@
if ($LASTEXITCODE -ne 0) { throw "Applying the update zip failed." }
Write-Host "If the service is running under deploy\windows\backend_supervisor.ps1 it will restart with the new files."
