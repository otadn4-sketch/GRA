# Stop only this project's backend supervisor and uvicorn. Does not stop the Bale crawler
# and does not kill unrelated python.exe processes.
# Compatible with Windows PowerShell 5.1 and PowerShell 7.

[CmdletBinding()]
param(
    [string]$TaskName = "GarayeNewsletter",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

Initialize-GarayeFolders
Set-Content -LiteralPath $script:StopFlag -Value ((Get-Date).ToUniversalTime().ToString("o")) -Encoding ASCII
Write-GarayeLog "Stop requested for backend on port $Port."

$task = Get-ScheduledTaskSafe -TaskName $TaskName
if ($task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

$supervisorPid = Get-RecordedPid -Path $script:SupervisorPidPath
if ($supervisorPid) {
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        if (-not (Get-Process -Id $supervisorPid -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 400
    }
}

$uvicornPid = Get-RecordedPid -Path $script:PidPath
if ($uvicornPid -and (Test-GarayeOurUvicorn -ProcessId $uvicornPid -Port $Port)) {
    Stop-GarayeProcessTree -ProcessId $uvicornPid
    Write-Host "Stopped uvicorn PID $uvicornPid."
}
elseif ($uvicornPid) {
    Write-GarayeLog -Level "WARN" -Message "PID file $uvicornPid is not this project's uvicorn; not killing it."
}

$listeners = @(Get-GarayePortListenerIds -Port $Port)
foreach ($listenerId in $listeners) {
    $listenerId = ConvertTo-GarayePid $listenerId
    if ($listenerId -le 0) { continue }
    if (Test-GarayeOurUvicorn -ProcessId $listenerId -Port $Port) {
        Write-GarayeLog "Stopping leftover LISTENING uvicorn pid=$listenerId"
        Stop-GarayeProcessTree -ProcessId $listenerId -Force
    }
    else {
        $cmd = Get-ProcessCommandLine -ProcessId $listenerId
        Write-GarayeLog -Level "WARN" -Message "Port $Port still LISTENING pid=$listenerId command=$cmd (foreign; left running)"
    }
}

if ($supervisorPid -and (Get-Process -Id $supervisorPid -ErrorAction SilentlyContinue)) {
    Stop-Process -Id $supervisorPid -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    if (Get-Process -Id $supervisorPid -ErrorAction SilentlyContinue) {
        Stop-GarayeProcessTree -ProcessId $supervisorPid -Force
    }
    Write-Host "Stopped supervisor PID $supervisorPid."
}

foreach ($path in @($script:PidPath, $script:SupervisorPidPath, $script:StopFlag)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Backend stop complete."
