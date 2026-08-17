[CmdletBinding()]
param(
    [string]$TaskName = "GarayeBaleCrawler"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pidPath = Join-Path $projectRoot "run\crawler.pid"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task -and $task.State -eq "Running") {
    Stop-ScheduledTask -TaskName $TaskName
    Write-Host "Scheduled crawler task stopped."
}
if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host "Crawler PID file not found; no manual crawler process was detected."
    exit 0
}
$crawlerPid = [int](Get-Content -LiteralPath $pidPath -Raw)
$process = Get-Process -Id $crawlerPid -ErrorAction SilentlyContinue
if ($process) {
    $expectedPython = (Resolve-Path (Join-Path $projectRoot ".venv\Scripts\python.exe")).Path
    if ($process.Path -and -not [string]::Equals(
        $process.Path, $expectedPython, [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Recorded PID does not belong to the Garaye virtual environment. Stop cancelled."
    }
    Stop-Process -Id $crawlerPid
    Write-Host "Bale crawler stopped."
}
Remove-Item -LiteralPath $pidPath -Force
