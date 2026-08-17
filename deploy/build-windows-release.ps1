[CmdletBinding()]
param(
    [string]$OutputDirectory = "",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$version = (Get-Content -LiteralPath (Join-Path $projectRoot "VERSION") -Raw).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') {
    throw "VERSION must use semantic versioning, for example 11.3.0."
}

$versionSlug = $version.Replace(".", "_")
$releaseName = "garaye_newsletter_v${versionSlug}_windows_release"
$outputRoot = if ($OutputDirectory) {
    [IO.Path]::GetFullPath($OutputDirectory)
}
else {
    (Split-Path -Parent $projectRoot)
}
$staging = Join-Path $outputRoot $releaseName
$zipPath = Join-Path $outputRoot "$releaseName.zip"
$hashPath = "$zipPath.sha256.txt"

if (Test-Path -LiteralPath $staging) {
    throw "Staging directory already exists: $staging"
}
if (Test-Path -LiteralPath $zipPath) {
    throw "Release ZIP already exists: $zipPath"
}
if (Test-Path -LiteralPath $hashPath) {
    throw "Release hash file already exists: $hashPath"
}

if (-not $SkipTests) {
    $python = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python)) {
        throw "Virtual environment was not found. Run deploy\install.ps1 first."
    }
    Push-Location $projectRoot
    try {
        & $python ".\tests\smoke_e2e.py"
        if ($LASTEXITCODE -ne 0) { throw "Smoke test failed." }
        & $python -m pip check
        if ($LASTEXITCODE -ne 0) { throw "pip check failed." }
    }
    finally {
        Pop-Location
    }
}

New-Item -ItemType Directory -Path $staging | Out-Null

$includedDirectories = @(
    "app",
    "assets",
    "crawler",
    "database",
    "deploy",
    "docs",
    "tests",
    "tools",
    "web"
)
$excludedSegments = @(
    "\__pycache__\",
    "\.venv\",
    "\qa\",
    "\data\",
    "\backups\",
    "\run\"
)

foreach ($directory in $includedDirectories) {
    $sourceDirectory = Join-Path $projectRoot $directory
    if (-not (Test-Path -LiteralPath $sourceDirectory -PathType Container)) {
        continue
    }
    Get-ChildItem -LiteralPath $sourceDirectory -Recurse -File | ForEach-Object {
        $relative = $_.FullName.Substring($projectRoot.Length + 1)
        $framed = "\" + $relative.Replace("/", "\") + "\"
        $excluded = $false
        foreach ($segment in $excludedSegments) {
            if ($framed.IndexOf($segment, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $excluded = $true
                break
            }
        }
        if (
            $excluded -or
            $_.Extension -eq ".pyc" -or
            $_.Name -like "*.db-wal" -or
            $_.Name -like "*.db-shm" -or
            $_.Name -like "test-*"
        ) {
            return
        }
        $destination = Join-Path $staging $relative
        $destinationParent = Split-Path -Parent $destination
        if (-not (Test-Path -LiteralPath $destinationParent)) {
            New-Item -ItemType Directory -Path $destinationParent | Out-Null
        }
        Copy-Item -LiteralPath $_.FullName -Destination $destination
    }
}

$rootFiles = @(
    ".env.example",
    "VERSION",
    "requirements.txt"
)
$rootFiles += (
    Get-ChildItem -LiteralPath $projectRoot -File |
    Where-Object {
        $_.Extension -in @(".md", ".ps1", ".bat", ".txt") -and
        $_.Name -ne ".env"
    } |
    Select-Object -ExpandProperty Name
)
$rootFiles | Sort-Object -Unique | ForEach-Object {
    $source = Join-Path $projectRoot $_
    if (Test-Path -LiteralPath $source -PathType Leaf) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $staging $_)
    }
}

foreach ($directory in @(
    "data",
    "data\bulletins",
    "data\crawler",
    "data\crawler\logs",
    "data\logs",
    "backups",
    "run"
)) {
    New-Item -ItemType Directory -Force -Path (Join-Path $staging $directory) |
        Out-Null
}

$forbidden = Get-ChildItem -LiteralPath $staging -Recurse -Force |
    Where-Object {
        $_.Name -eq ".env" -or
        $_.Name -eq "__pycache__" -or
        $_.Extension -eq ".pyc" -or
        $_.Name -like "*.db-wal" -or
        $_.Name -like "*.db-shm" -or
        $_.Name -like "test-*" -or
        $_.Name -like "*.log" -or
        $_.Name -like "*.pid"
    }
if ($forbidden) {
    $names = ($forbidden | Select-Object -ExpandProperty FullName) -join [Environment]::NewLine
    throw "Forbidden release artifacts were found:`n$names"
}

Compress-Archive -LiteralPath $staging -DestinationPath $zipPath -CompressionLevel Optimal
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
Set-Content -LiteralPath $hashPath -Value "$hash  $([IO.Path]::GetFileName($zipPath))" -Encoding ASCII

Write-Host "Release directory: $staging"
Write-Host "Release ZIP:       $zipPath"
Write-Host "SHA256:            $hash"

