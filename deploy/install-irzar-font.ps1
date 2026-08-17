# Requires Run as Administrator and a properly licensed font file.
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$FontPath)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $FontPath -PathType Leaf)) {
    throw "IRZar font file was not found at '$FontPath'. Run Test-Path -LiteralPath '<real-path-to-IRZar.ttf>' and pass a path that returns True."
}
$resolved = (Resolve-Path -LiteralPath $FontPath).Path
if ([IO.Path]::GetExtension($resolved).ToLowerInvariant() -notin @(".ttf", ".otf")) {
    throw "Font file must be TTF or OTF."
}
$fontName = "IRZar" + [IO.Path]::GetExtension($resolved).ToLowerInvariant()
$target = Join-Path $env:WINDIR "Fonts\$fontName"
Copy-Item -LiteralPath $resolved -Destination $target -Force
New-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts" `
    -Name "IRZar (TrueType)" -Value $fontName -PropertyType String -Force | Out-Null
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$webFontDir = Join-Path $projectRoot "web\assets\fonts"
if (-not (Test-Path -LiteralPath $webFontDir)) {
    New-Item -ItemType Directory -Path $webFontDir | Out-Null
}
Copy-Item -LiteralPath $resolved -Destination (Join-Path $projectRoot "web\assets\fonts\IRZar.ttf") -Force
Write-Host "IRZar was installed for Windows and the web UI. Restart the service and Word."
