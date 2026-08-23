# Production supervisor for the Garaye FastAPI/Uvicorn backend on Windows Server.
# Compatible with Windows PowerShell 5.1 and PowerShell 7.
#
# This process stays in the foreground (Scheduled Task / -Supervised).
# It starts only THIS project's .venv uvicorn, probes GET /health every 30s,
# and restarts the child if the process dies or health fails 3 times in a row.

[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [int]$HealthIntervalSeconds = 30,
    [int]$HealthTimeoutSeconds = 10,
    [int]$FailThreshold = 3,
    [int]$StartupGraceSeconds = 45,
    [int]$CooldownSeconds = 15
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_common.ps1")

if ($HostAddress -in @("0.0.0.0", "::", "[::]")) {
    Write-Host "Refusing public bind $HostAddress; using 127.0.0.1 so Caddy remains the only Internet listener."
    $HostAddress = "127.0.0.1"
}

Initialize-GarayeFolders
$python = Get-GarayePython
$healthUri = Get-GarayeHealthUri -HostAddress $HostAddress -Port $Port
$mutexName = ConvertTo-GarayeMutexName -ProjectRoot $script:ProjectRoot
$mutex = $null
$ownsMutex = $false
$child = $null
try {
    $mutex = New-Object System.Threading.Mutex($false, $mutexName)
}
catch {
    $mutexName = $mutexName.Replace("Global\", "Local\")
    Write-GarayeLog -Level "WARN" -Message "Falling back to local mutex $mutexName"
    $mutex = New-Object System.Threading.Mutex($false, $mutexName)
}

function Install-UpdatedRequirements {
    if (-not (Test-Path -LiteralPath $script:PipFlag)) { return }
    Write-GarayeLog "Installing updated Python requirements before restart."
    $env:PYTHONIOENCODING = "utf-8"
    $req = Join-Path $script:ProjectRoot "requirements.txt"
    & $python -m pip install --disable-pip-version-check --no-color -r $req
    if ($LASTEXITCODE -ne 0) {
        Write-GarayeLog -Level "WARN" -Message "pip install after live update failed; will retry on the next restart."
        return
    }
    Remove-Item -LiteralPath $script:PipFlag -Force -ErrorAction SilentlyContinue
}

function Start-GarayeChild {
    foreach ($logFile in @($script:UvicornOutLog, $script:UvicornErrLog)) {
        if (Test-Path -LiteralPath $logFile) {
            $archive = "{0}.{1}" -f $logFile, (Get-Date -Format "yyyyMMdd-HHmmss")
            try {
                Move-Item -LiteralPath $logFile -Destination $archive -Force
            }
            catch {
                Move-GarayeLogIfLarge -Path $logFile
            }
        }
    }
    Start-Sleep -Seconds 1
    $outLog = $script:UvicornOutLog
    $errLog = $script:UvicornErrLog
    $arguments = @(
        "-m", "uvicorn", "app.main:app",
        "--host", $HostAddress,
        "--port", [string]$Port,
        "--proxy-headers",
        "--forwarded-allow-ips", "127.0.0.1"
    )
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUNBUFFERED = "1"
    try {
        $process = Start-Process -FilePath $python -ArgumentList $arguments `
            -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $outLog `
            -RedirectStandardError $errLog
    }
    catch {
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $outLog = Join-Path $script:LogDir "uvicorn.out.$stamp.log"
        $errLog = Join-Path $script:LogDir "uvicorn.err.$stamp.log"
        Write-GarayeLog -Level "WARN" -Message "Previous uvicorn log files were locked. Using $outLog"
        $process = Start-Process -FilePath $python -ArgumentList $arguments `
            -WorkingDirectory $script:ProjectRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $outLog `
            -RedirectStandardError $errLog
    }
    Set-Content -LiteralPath $script:PidPath -Value $process.Id -Encoding ASCII
    Write-GarayeLog "Started uvicorn pid=$($process.Id) host=$HostAddress port=$Port"
    return $process
}

function Stop-GarayeChild {
    param(
        [System.Diagnostics.Process]$Process,
        [switch]$Force
    )
    $targetIds = New-Object System.Collections.Generic.List[int]
    if ($Process -and -not $Process.HasExited) {
        [void]$targetIds.Add([int]$Process.Id)
    }
    $recorded = Get-RecordedPid -Path $script:PidPath
    if ($recorded -and -not $targetIds.Contains($recorded)) {
        [void]$targetIds.Add($recorded)
    }
    foreach ($processId in $targetIds) {
        if (-not (Test-GarayeOurUvicorn -ProcessId $processId -Port $Port)) {
            if (Get-Process -Id $processId -ErrorAction SilentlyContinue) {
                Write-GarayeLog -Level "WARN" -Message "Refusing to stop PID $processId because it is not this project's uvicorn."
            }
            continue
        }
        Write-GarayeLog "Stopping backend process tree pid=$processId force=$Force"
        Stop-GarayeProcessTree -ProcessId $processId -Force:$Force
    }
}

function Wait-GarayeStartup {
    param([System.Diagnostics.Process]$Process)
    $deadline = (Get-Date).AddSeconds($StartupGraceSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) {
            Write-GarayeLog -Level "ERROR" -Message "uvicorn exited during startup. ExitCode=$($Process.ExitCode)"
            return $false
        }
        if (Test-GarayeHealthOk -HealthUri $healthUri -TimeoutSec $HealthTimeoutSeconds) {
            Write-GarayeLog "Health OK after startup pid=$($Process.Id) uri=$healthUri"
            return $true
        }
        Start-Sleep -Seconds 1
        if (Test-Path -LiteralPath $script:StopFlag) { return $false }
    }
    Write-GarayeLog -Level "ERROR" -Message "Health check did not succeed within $StartupGraceSeconds seconds."
    return $false
}

try {
    try {
        $ownsMutex = $mutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $ownsMutex = $true
        Write-GarayeLog -Level "WARN" -Message "Took over an abandoned supervisor mutex."
    }
    if (-not $ownsMutex) {
        throw "Another Garaye backend supervisor is already running for $script:ProjectRoot."
    }

    $existingSupervisor = Get-RecordedPid -Path $script:SupervisorPidPath
    if ($existingSupervisor -and $existingSupervisor -ne $PID) {
        $alive = Get-Process -Id $existingSupervisor -ErrorAction SilentlyContinue
        if ($alive) {
            throw "Garaye supervisor PID file already belongs to live process $existingSupervisor."
        }
    }
    Set-Content -LiteralPath $script:SupervisorPidPath -Value $PID -Encoding ASCII
    if (Test-Path -LiteralPath $script:StopFlag) {
        Remove-Item -LiteralPath $script:StopFlag -Force -ErrorAction SilentlyContinue
    }

    Write-GarayeLog "Supervisor starting pid=$PID root=$script:ProjectRoot python=$python listen=${HostAddress}:$Port health=$healthUri"
    $child = $null
    $failures = 0
    $backoff = $CooldownSeconds
    $nextHealth = Get-Date

    while (-not (Test-Path -LiteralPath $script:StopFlag)) {
        if (Test-Path -LiteralPath $script:ReloadFlag) {
            Remove-Item -LiteralPath $script:ReloadFlag -Force -ErrorAction SilentlyContinue
            Write-GarayeLog "Live-update reload flag detected."
            if ($child -and -not $child.HasExited) {
                Stop-GarayeChild -Process $child
            }
            $child = $null
        }

        $needStart = $false
        if (-not $child -or $child.HasExited) {
            $recorded = Get-RecordedPid -Path $script:PidPath
            $adopted = $null
            if ($recorded -and (Test-GarayeOurUvicorn -ProcessId $recorded -Port $Port)) {
                if (Test-GarayeHealthOk -HealthUri $healthUri -TimeoutSec $HealthTimeoutSeconds) {
                    $adopted = Get-Process -Id $recorded -ErrorAction SilentlyContinue
                    if ($adopted) {
                        $child = $adopted
                        Write-GarayeLog "Adopted healthy existing uvicorn pid=$recorded"
                    }
                }
                else {
                    Write-GarayeLog -Level "WARN" -Message "Recorded uvicorn pid=$recorded is not healthy; restarting that process tree."
                    Stop-GarayeProcessTree -ProcessId $recorded -Force
                }
            }

            if (-not $child -or $child.HasExited) {
                $listeners = ConvertTo-GarayePidArray (Get-ListeningPids -Port $Port)
                $foreign = $false
                foreach ($listenerId in $listeners) {
                    if (Test-GarayeOurUvicorn -ProcessId $listenerId -Port $Port) {
                        if (Test-GarayeHealthOk -HealthUri $healthUri -TimeoutSec $HealthTimeoutSeconds) {
                            $adopted = Get-Process -Id $listenerId -ErrorAction SilentlyContinue
                            if ($adopted) {
                                $child = $adopted
                                Set-Content -LiteralPath $script:PidPath -Value $listenerId -Encoding ASCII
                                Write-GarayeLog "Adopted LISTENING uvicorn pid=$listenerId"
                            }
                        }
                        else {
                            Write-GarayeLog -Level "WARN" -Message "Our uvicorn pid=$listenerId is LISTENING but health failed; terminating it."
                            Stop-GarayeProcessTree -ProcessId $listenerId -Force
                        }
                    }
                    else {
                        $foreign = $true
                        $cmd = Get-ProcessCommandLine -ProcessId $listenerId
                        Write-GarayeLog -Level "ERROR" -Message "Port $Port is occupied by a foreign process pid=$listenerId command=$cmd . Not killing it."
                    }
                }
                if ($foreign -and (-not $child -or $child.HasExited)) {
                    Start-Sleep -Seconds $backoff
                    continue
                }
                $needStart = -not $child -or $child.HasExited
            }
        }

        if ($needStart) {
            Install-UpdatedRequirements
            $listeners = ConvertTo-GarayePidArray (Get-ListeningPids -Port $Port)
            $blocked = $false
            foreach ($listenerId in $listeners) {
                if (-not (Test-GarayeOurUvicorn -ProcessId $listenerId -Port $Port)) {
                    $cmd = Get-ProcessCommandLine -ProcessId $listenerId
                    Write-GarayeLog -Level "ERROR" -Message "Refusing to start because port $Port is held by pid=$listenerId command=$cmd"
                    $blocked = $true
                }
            }
            if ($blocked) {
                Start-Sleep -Seconds $backoff
                continue
            }
            $child = Start-GarayeChild
            if (-not (Wait-GarayeStartup -Process $child)) {
                Stop-GarayeChild -Process $child -Force
                $child = $null
                Write-GarayeLog -Level "ERROR" -Message "Startup failed. Cooling down ${backoff}s before retry."
                Start-Sleep -Seconds $backoff
                if ($backoff -lt 60) { $backoff = [Math]::Min(60, $backoff * 2) }
                continue
            }
            $failures = 0
            $backoff = $CooldownSeconds
            $nextHealth = (Get-Date).AddSeconds($HealthIntervalSeconds)
        }

        if (Test-Path -LiteralPath $script:StopFlag) { break }

        Start-Sleep -Seconds 1
        if (-not $child) { continue }
        try { $child.Refresh() } catch { $child = $null; continue }

        if ($child.HasExited) {
            Write-GarayeLog -Level "WARN" -Message "uvicorn pid=$($child.Id) exited unexpectedly. ExitCode=$($child.ExitCode). Restarting after ${CooldownSeconds}s."
            $child = $null
            Start-Sleep -Seconds $CooldownSeconds
            continue
        }

        if ((Get-Date) -lt $nextHealth) { continue }

        $healthy = Test-GarayeHealthOk -HealthUri $healthUri -TimeoutSec $HealthTimeoutSeconds
        $nextHealth = (Get-Date).AddSeconds($HealthIntervalSeconds)
        if ($healthy) {
            if ($failures -gt 0) {
                Write-GarayeLog "Health recovered after $failures failed check(s)."
            }
            $failures = 0
            $backoff = $CooldownSeconds
            continue
        }

        $failures++
        $listenPids = ConvertTo-GarayePidArray (Get-ListeningPids -Port $Port)
        $listenText = if ($listenPids.Count -gt 0) { ($listenPids -join ",") } else { "none" }
        Write-GarayeLog -Level "WARN" -Message "Health check failed ($failures/$FailThreshold) uri=$healthUri pid=$($child.Id) listeningPids=$listenText"

        if ($failures -lt $FailThreshold) { continue }

        Write-GarayeLog -Level "ERROR" -Message "Consecutive health failures reached $FailThreshold. Terminating uvicorn tree pid=$($child.Id) and restarting."
        Stop-GarayeChild -Process $child -Force
        $child = $null
        $failures = 0
        Write-GarayeLog "Cooldown ${CooldownSeconds}s after supervised restart."
        Start-Sleep -Seconds $CooldownSeconds
    }

    Write-GarayeLog "Supervisor stop requested."
}
catch {
    Write-GarayeLog -Level "ERROR" -Message ("Supervisor error: " + $_.Exception.Message)
    throw
}
finally {
    try {
        if ($child) { Stop-GarayeChild -Process $child }
    }
    catch {
        Write-GarayeLog -Level "ERROR" -Message ("Error while stopping child: " + $_.Exception.Message)
    }
    if (Test-Path -LiteralPath $script:SupervisorPidPath) {
        $recorded = Get-RecordedPid -Path $script:SupervisorPidPath
        if ($recorded -eq $PID) {
            Remove-Item -LiteralPath $script:SupervisorPidPath -Force -ErrorAction SilentlyContinue
        }
    }
    if (Test-Path -LiteralPath $script:StopFlag) {
        Remove-Item -LiteralPath $script:StopFlag -Force -ErrorAction SilentlyContinue
    }
    if ($ownsMutex) {
        try { $mutex.ReleaseMutex() } catch { }
    }
    if ($mutex) { $mutex.Dispose() }
    Write-GarayeLog "Supervisor shutdown complete pid=$PID"
}
