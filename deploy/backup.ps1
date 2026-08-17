$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
Set-Location -LiteralPath $projectRoot
$env:PYTHONIOENCODING = "utf-8"
& $python -c "import asyncio; from app.config import load_settings; from app.backup import create_backup; s=load_settings(); print(asyncio.run(create_backup(s.database_path,s.backup_dir)))"
if ($LASTEXITCODE -ne 0) { throw "Backup failed." }
