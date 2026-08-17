$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Virtual environment not found. Run deploy\install.ps1 first."
}
Set-Location -LiteralPath $projectRoot
$env:PYTHONIOENCODING = "utf-8"
foreach ($folder in @("data\crawler", "data\crawler\logs")) {
    if (-not (Test-Path -LiteralPath $folder)) {
        New-Item -ItemType Directory -Path $folder | Out-Null
    }
}
$crawlerDb = Join-Path $projectRoot "data\crawler\bale_forward_state.db"
$legacyCrawlerDb = Join-Path $projectRoot "crawler\legacy_state\bale_forward_state.db"
if (-not (Test-Path -LiteralPath $crawlerDb) -and (Test-Path -LiteralPath $legacyCrawlerDb)) {
    Copy-Item -LiteralPath $legacyCrawlerDb -Destination $crawlerDb
    Write-Host "Legacy Bale crawler boundary database was imported."
}
& $python -c "import asyncio; from app.config import load_settings; from app.db import Database; s=load_settings(); asyncio.run(Database(s.database_path).init()); print('Database initialized:', s.database_path)"
if ($LASTEXITCODE -ne 0) { throw "Database initialization failed. Check .env." }
Write-Host "Garaye v11.26.0 database is ready."
