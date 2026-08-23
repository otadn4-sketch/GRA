[CmdletBinding()]
param(
    [string]$PythonCommand = "",
    [string]$IRZarFontPath = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $projectRoot

if (-not $PythonCommand) {
    $pyLauncher = Get-Command "py.exe" -ErrorAction SilentlyContinue
    $pythonExe = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $PythonCommand = $pyLauncher.Source
        $pythonArgs = @("-3")
    }
    elseif ($pythonExe) {
        $PythonCommand = $pythonExe.Source
        $pythonArgs = @()
    }
    else {
        throw "Python 3.11+ was not found. Install Python x64 and enable Add to PATH."
    }
}
else {
    $resolvedPython = Get-Command $PythonCommand -ErrorAction Stop
    $PythonCommand = $resolvedPython.Source
    $pythonArgs = @()
}

Write-Host "Checking Python version..."
& $PythonCommand @pythonArgs -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'; print(sys.version)"
if ($LASTEXITCODE -ne 0) { throw "Python version is not supported." }

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    Write-Host "Creating virtual environment..."
    & $PythonCommand @pythonArgs -m venv ".venv"
    if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed." }
}

$venvPython = (Resolve-Path ".venv\Scripts\python.exe").Path
Write-Host "Installing dependencies..."
$env:PYTHONIOENCODING = "utf-8"
& $venvPython -m pip install --disable-pip-version-check --no-color --upgrade pip
& $venvPython -m pip install --disable-pip-version-check --no-color -r "requirements.txt"
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependency installation or validation failed." }

$layoutBrowsers = @(
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
) | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) }
if ($layoutBrowsers.Count -gt 0) {
    Write-Host "HTML/PDF layout browser: $($layoutBrowsers[0])"
}
else {
    Write-Warning "Edge/Chrome was not found. HTML output will work, but direct PDF needs a Chromium browser. Install Microsoft Edge or set LAYOUT_BROWSER_PATH in .env."
}

foreach ($folder in @("data", "data\bulletins", "data\crawler", "data\crawler\logs", "data\logs", "backups", "run")) {
    if (-not (Test-Path -LiteralPath $folder)) {
        New-Item -ItemType Directory -Path $folder | Out-Null
    }
}

if (-not (Test-Path -LiteralPath ".env")) {
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Warning ".env was created. Set ADMIN_PASSWORD and both AI profiles before startup."
}
else {
    Write-Host "Existing .env was preserved."
}

if ($IRZarFontPath) {
    if (-not (Test-Path -LiteralPath $IRZarFontPath -PathType Leaf)) {
        throw "IRZar font file was not found at '$IRZarFontPath'. Run Test-Path -LiteralPath '<real-path-to-IRZar.ttf>' and pass a path that returns True."
    }
    $fontSource = (Resolve-Path -LiteralPath $IRZarFontPath).Path
    $fontExtension = [IO.Path]::GetExtension($fontSource).ToLowerInvariant()
    if ($fontExtension -notin @(".ttf", ".otf")) {
        throw "IRZar font must be a .ttf or .otf file."
    }
    Copy-Item -LiteralPath $fontSource -Destination "web\assets\fonts\IRZar.ttf" -Force
    Write-Host "IRZar was copied into the web application."
}
elseif (-not (Test-Path -LiteralPath "web\assets\fonts\IRZar.ttf")) {
    Write-Warning "IRZar.ttf was not supplied. Put a licensed copy at web\assets\fonts\IRZar.ttf."
}

Write-Host ""
Write-Host "Initial installation completed."
Write-Host "1) Edit .env."
Write-Host "2) Run deploy\initialize.ps1."
Write-Host "3) Run deploy\windows\install_backend_task.ps1 from an elevated PowerShell (recommended), or deploy\windows\start_backend.ps1, then open https://prasad.lmskalk.ir or http://127.0.0.1:8000/login."
Write-Host "   Production binds Uvicorn to 127.0.0.1:8000. Caddy on 80/443 is the public entry."
Write-Host "   See deploy\windows\README.md."
Write-Host "4) If BALE_CRAWLER_ENABLED=true, run deploy\test-crawler-config.ps1."
