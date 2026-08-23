# Shared helpers for Garaye Windows backend scripts.
# Compatible with Windows PowerShell 5.1 and PowerShell 7.

$script:WindowsDir = $PSScriptRoot
$script:ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$script:PythonExe = Join-Path $script:ProjectRoot ".venv\Scripts\python.exe"
$script:RunDir = Join-Path $script:ProjectRoot "run"
$script:LogDir = Join-Path $script:ProjectRoot "logs"
$script:PidPath = Join-Path $script:RunDir "garaye.pid"
$script:SupervisorPidPath = Join-Path $script:RunDir "garaye-supervisor.pid"
$script:StopFlag = Join-Path $script:RunDir "stop.request"
$script:ReloadFlag = Join-Path $script:RunDir "reload.request"
$script:PipFlag = Join-Path $script:RunDir "pip.request"
$script:SupervisorLog = Join-Path $script:LogDir "backend-supervisor.log"
$script:UvicornOutLog = Join-Path $script:LogDir "uvicorn.out.log"
$script:UvicornErrLog = Join-Path $script:LogDir "uvicorn.err.log"
$script:DefaultPort = 8000
$script:DefaultHost = "127.0.0.1"
$script:DefaultTaskName = "GarayeNewsletter"
$script:MutexPrefix = "Global\GarayeBackendSupervisor"

function Initialize-GarayeFolders {
    foreach ($folder in @($script:RunDir, $script:LogDir)) {
        if (-not (Test-Path -LiteralPath $folder)) {
            New-Item -ItemType Directory -Path $folder | Out-Null
        }
    }
}

function Get-GarayePython {
    if (-not (Test-Path -LiteralPath $script:PythonExe)) {
        throw "Virtual environment not found at $($script:PythonExe). Run deploy\install.ps1 first."
    }
    return (Resolve-Path -LiteralPath $script:PythonExe).Path
}

function ConvertTo-GarayeMutexName {
    param([string]$ProjectRoot)
    $sha1 = [System.Security.Cryptography.SHA1]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($ProjectRoot.ToLowerInvariant())
        $hash = [BitConverter]::ToString($sha1.ComputeHash($bytes)).Replace("-", "")
    }
    finally {
        $sha1.Dispose()
    }
    return "$($script:MutexPrefix)_$hash"
}

function Write-GarayeLog {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ValidateSet("DEBUG", "INFO", "WARN", "ERROR")]
        [string]$Level = "INFO"
    )
    Initialize-GarayeFolders
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "{0} [{1}] {2}" -f $stamp, $Level, $Message
    $logFile = $script:SupervisorLog
    if (Test-Path -LiteralPath $logFile) {
        $item = Get-Item -LiteralPath $logFile -ErrorAction SilentlyContinue
        if ($item -and $item.Length -gt 5MB) {
            for ($index = 4; $index -ge 1; $index--) {
                $src = "{0}.{1}" -f $logFile, $index
                $dst = "{0}.{1}" -f $logFile, ($index + 1)
                if (Test-Path -LiteralPath $src) {
                    Move-Item -LiteralPath $src -Destination $dst -Force
                }
            }
            Move-Item -LiteralPath $logFile -Destination ($logFile + ".1") -Force
        }
    }
    $encoding = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::AppendAllText($logFile, $line + [Environment]::NewLine, $encoding)
    Write-Host $line
}

function Move-GarayeLogIfLarge {
    param(
        [string]$Path,
        [int]$MaxBytes = 10485760
    )
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    if (-not $item -or $item.Length -le $MaxBytes) { return }
    $archive = "{0}.{1}" -f $Path, (Get-Date -Format "yyyyMMdd-HHmmss")
    try {
        Move-Item -LiteralPath $Path -Destination $archive -Force
    }
    catch {
        Write-GarayeLog -Level "WARN" -Message "Could not rotate log file $Path : $($_.Exception.Message)"
    }
}

function Get-RecordedPid {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $raw = (Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue)
    if (-not $raw) { return $null }
    $value = 0
    if (-not [int]::TryParse($raw.Trim(), [ref]$value)) { return $null }
    if ($value -le 0) { return $null }
    return $value
}

function ConvertTo-GarayePidArray {
    param($Value)
    $out = New-Object System.Collections.Generic.List[int]
    $queue = New-Object System.Collections.Queue
    [void]$queue.Enqueue($Value)
    while ($queue.Count -gt 0) {
        $cur = $queue.Dequeue()
        if ($null -eq $cur -or $cur -eq "") { continue }
        if ($cur -is [System.Array] -or ($cur -is [System.Collections.IEnumerable] -and -not ($cur -is [string]))) {
            foreach ($item in @($cur)) { [void]$queue.Enqueue($item) }
            continue
        }
        $n = 0
        if ([int]::TryParse("$cur", [ref]$n) -and $n -gt 0 -and -not $out.Contains($n)) {
            [void]$out.Add($n)
        }
    }
    return $out
}

function ConvertTo-GarayePid {
    param($Value)
    foreach ($n in (ConvertTo-GarayePidArray $Value)) { return [int]$n }
    return 0
}

function Get-GarayeProcess {
    param($ProcessId)
    $id = ConvertTo-GarayePid $ProcessId
    if ($id -le 0) { return $null }
    return Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
}

function Test-GarayeOurUvicorn {
    param(
        [Parameter(Mandatory = $true)]$ProcessId,
        [int]$Port = 8000
    )
    $ProcessId = ConvertTo-GarayePid $ProcessId
    if ($ProcessId -le 0) { return $false }
    $proc = Get-GarayeProcess -ProcessId $ProcessId
    if (-not $proc) { return $false }
    $python = $null
    try { $python = Get-GarayePython } catch { return $false }
    $exe = [string]$proc.ExecutablePath
    $cmd = [string]$proc.CommandLine
    $exeOk = $false
    if ($exe) {
        $exeOk = [string]::Equals($exe, $python, [System.StringComparison]::OrdinalIgnoreCase)
    }
    elseif ($cmd) {
        $exeOk = $cmd.IndexOf($python, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    }
    if (-not $exeOk) { return $false }
    if ($cmd -notmatch 'uvicorn') { return $false }
    if ($cmd -notmatch 'app\.main:app') { return $false }
    if ($cmd -notmatch [regex]::Escape(":$Port") -and $cmd -notmatch "-port $Port" -and $cmd -notmatch "--port $Port") {
        return $false
    }
    return $true
}

function Get-ListeningPids {
    param([int]$Port)
    $ids = New-Object System.Collections.Generic.List[int]
    $pattern = ":$Port\s"
    foreach ($line in @(netstat -ano -p tcp)) {
        if ($line -notmatch $pattern) { continue }
        if ($line -notmatch "LISTENING\s+(\d+)\s*$") { continue }
        $owning = 0
        if ([int]::TryParse($Matches[1], [ref]$owning) -and $owning -gt 0 -and -not $ids.Contains($owning)) {
            [void]$ids.Add($owning)
        }
    }
    return $ids
}

function Grant-GarayeSystemAccess {
    $grant = icacls.exe $script:ProjectRoot /grant "NT AUTHORITY\SYSTEM:(OI)(CI)M" /T /C /Q 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-GarayeLog -Level "WARN" -Message "Could not grant SYSTEM modify on project folder: $grant"
    }
}

function Get-ProcessCommandLine {
    param($ProcessId)
    $ProcessId = ConvertTo-GarayePid $ProcessId
    if ($ProcessId -le 0) { return $null }
    $proc = Get-GarayeProcess -ProcessId $ProcessId
    if (-not $proc) { return $null }
    $exe = [string]$proc.ExecutablePath
    $cmd = [string]$proc.CommandLine
    if ($cmd) { return $cmd }
    if ($exe) { return $exe }
    return "(command line unavailable for PID $ProcessId)"
}

function Write-ForeignListenerWarning {
    param([int]$Port)
    $pids = ConvertTo-GarayePidArray (Get-ListeningPids -Port $Port)
    if ($pids.Count -eq 0) { return $false }
    foreach ($processId in $pids) {
        $cmd = Get-ProcessCommandLine -ProcessId $processId
        Write-GarayeLog -Level "ERROR" -Message "Port $Port is already LISTENING pid=$processId command=$cmd"
    }
    return $true
}

function Stop-GarayeProcessTree {
    param(
        $ProcessId,
        [switch]$Force
    )
    $ProcessId = ConvertTo-GarayePid $ProcessId
    if ($ProcessId -le 0) { return }
    if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return }
    if ($Force) {
        & "$env:SystemRoot\System32\taskkill.exe" /PID $ProcessId /T /F | Out-Null
        return
    }
    Stop-Process -Id $ProcessId -ErrorAction SilentlyContinue
    for ($attempt = 1; $attempt -le 16; $attempt++) {
        Start-Sleep -Milliseconds 500
        if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return }
    }
    & "$env:SystemRoot\System32\taskkill.exe" /PID $ProcessId /T /F | Out-Null
}

function Test-GarayeHealthOk {
    param(
        [string]$HealthUri,
        [int]$TimeoutSec = 10
    )
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Method Get -Uri $HealthUri -TimeoutSec $TimeoutSec
        if ([int]$response.StatusCode -ne 200) { return $false }
        $body = $response.Content
        if ($body -match '"status"\s*:\s*"ok"') { return $true }
        if ($body -match '"ok"\s*:\s*true') { return $true }
        return $false
    }
    catch {
        return $false
    }
}

function Get-GarayeHealthUri {
    param(
        [string]$HostAddress = "127.0.0.1",
        [int]$Port = 8000
    )
    $probeHost = $HostAddress
    if ($HostAddress -in @("0.0.0.0", "::", "[::]")) {
        $probeHost = "127.0.0.1"
    }
    return "http://${probeHost}:$Port/health"
}

function Get-ScheduledTaskSafe {
    param([string]$TaskName)
    return Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}
