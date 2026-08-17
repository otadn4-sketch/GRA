$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runDir = Join-Path $projectRoot "run"
$pidPath = Join-Path $runDir "garaye.pid"
$supervisorPidPath = Join-Path $runDir "garaye-supervisor.pid"
$stopFlag = Join-Path $runDir "stop.request"
if (-not (Test-Path -LiteralPath $runDir)) {
    New-Item -ItemType Directory -Path $runDir | Out-Null
}
Set-Content -LiteralPath $stopFlag -Value ((Get-Date).ToUniversalTime().ToString("o")) -Encoding ASCII

function Stop-RecordedProcess([string]$Path, [string]$ExpectedExecutable) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $servicePid = [int](Get-Content -LiteralPath $Path -Raw)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$servicePid" -ErrorAction SilentlyContinue
    if ($process) {
        if ($ExpectedExecutable) {
            $expectedPython = (Resolve-Path -LiteralPath $ExpectedExecutable).Path
            if (-not [string]::Equals($process.ExecutablePath, $expectedPython, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "Recorded PID does not belong to the Garaye virtual environment. Stop cancelled."
            }
        }
        Stop-Process -Id $servicePid -ErrorAction SilentlyContinue
        Write-Host "Stopped process $servicePid."
    }
    Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    return [bool]$process
}

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$stoppedService = $false
if (Test-Path -LiteralPath $python) {
    $stoppedService = Stop-RecordedProcess $pidPath $python
}
else {
    $stoppedService = Stop-RecordedProcess $pidPath ""
}
if (Test-Path -LiteralPath $supervisorPidPath) {
    $supervisorPid = [int](Get-Content -LiteralPath $supervisorPidPath -Raw)
    $supervisor = Get-Process -Id $supervisorPid -ErrorAction SilentlyContinue
    if ($supervisor) {
        for ($attempt = 1; $attempt -le 10; $attempt++) {
            Start-Sleep -Milliseconds 400
            if (-not (Get-Process -Id $supervisorPid -ErrorAction SilentlyContinue)) { break }
        }
        Stop-Process -Id $supervisorPid -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $supervisorPidPath -Force -ErrorAction SilentlyContinue
}
if (-not $stoppedService) {
    Write-Host "Web service PID file not found."
}
else {
    Write-Host "Garaye service stopped."
}
& (Join-Path $PSScriptRoot "stop-crawler.ps1")
