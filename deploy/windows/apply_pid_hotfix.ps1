# Apply on the Windows server if git pull did not update deploy\windows files.
# Fixes: Test-GarayeOurUvicorn ProcessId received System.Int32[] and supervisor exited
# before uvicorn could bind 127.0.0.1:8000.
#
# Usage (from the project root):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\apply_pid_hotfix.ps1

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
if (-not $here) {
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
}
$root = Split-Path (Split-Path $here -Parent) -Parent
if (-not (Test-Path -LiteralPath (Join-Path $here "_common.ps1"))) {
    throw "Run this script from deploy\windows or keep it next to _common.ps1."
}

$commonPath = Join-Path $here "_common.ps1"
$supPath = Join-Path $here "backend_supervisor.ps1"
$statusPath = Join-Path $here "status_backend.ps1"
$stopPath = Join-Path $here "stop_backend.ps1"
$utf8 = New-Object System.Text.UTF8Encoding $false

function Save-Text([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, $utf8)
}

function Get-Text([string]$Path) {
    return [System.IO.File]::ReadAllText($Path)
}

$pidHelpers = @'
function ConvertTo-GarayePid {
    param($Value)
    if ($null -eq $Value) { return 0 }
    $cur = $Value
    for ($i = 0; $i -lt 8; $i++) {
        if ($null -eq $cur) { return 0 }
        if ($cur -is [string] -or $cur -is [ValueType]) { break }
        $next = $null
        foreach ($item in $cur) { $next = $item; break }
        if ($null -eq $next -or [object]::ReferenceEquals($next, $cur)) { break }
        $cur = $next
    }
    $n = 0
    if ([int]::TryParse("$cur", [ref]$n) -and $n -gt 0) { return [int]$n }
    return 0
}

function ConvertTo-GarayePidArray {
    param($Value)
    if ($null -eq $Value) { return }
    $walk = New-Object System.Collections.ArrayList
    [void]$walk.Add($Value)
    $index = 0
    $seen = @{}
    while ($index -lt $walk.Count -and $index -lt 256) {
        $cur = $walk[$index]
        $index++
        if ($null -eq $cur -or $cur -eq "") { continue }
        if ($cur -is [string] -or $cur -is [ValueType]) {
            $n = 0
            if ([int]::TryParse("$cur", [ref]$n) -and $n -gt 0 -and -not $seen.ContainsKey($n)) {
                $seen[$n] = $true
                Write-Output $n
            }
            continue
        }
        if ($cur -is [System.Array] -or $cur -is [System.Collections.IList]) {
            foreach ($item in $cur) { [void]$walk.Add($item) }
            continue
        }
        $n = 0
        if ([int]::TryParse("$cur", [ref]$n) -and $n -gt 0 -and -not $seen.ContainsKey($n)) {
            $seen[$n] = $true
            Write-Output $n
        }
    }
}

function Get-GarayePortListenerIds {
    param([int]$Port)
    ConvertTo-GarayePidArray (Get-ListeningPids -Port $Port)
}

'@

$listeningBody = @'
function Get-ListeningPids {
    param([int]$Port)
    $seen = @{}
    $pattern = ":$Port\s"
    foreach ($line in @(netstat -ano -p tcp)) {
        if ($line -notmatch $pattern) { continue }
        if ($line -notmatch "LISTENING\s+(\d+)\s*$") { continue }
        $owning = 0
        if ([int]::TryParse($Matches[1], [ref]$owning) -and $owning -gt 0 -and -not $seen.ContainsKey($owning)) {
            $seen[$owning] = $true
            Write-Output $owning
        }
    }
}
'@

$flatten = @'
$listeners = @()
foreach ($raw in @(Get-ListeningPids -Port $Port)) {
    foreach ($item in @($raw)) {
        $n = 0
        if ([int]::TryParse("$item", [ref]$n) -and $n -gt 0) { $listeners += $n }
    }
}
'@

$common = Get-Text $commonPath

# 1) Never return a unary-wrapped Int32[].
$common = $common.Replace('return ,$ids.ToArray()', 'foreach ($id in $ids) { Write-Output ([int]$id) }')
$common = $common.Replace('return ,$ids.ToArray()', 'foreach ($id in $ids) { Write-Output ([int]$id) }')
# PS 5.1: @($genericList) re-wraps the list as one item.
$common = $common.Replace('foreach ($item in @($cur)) { [void]$queue.Enqueue($item) }', 'foreach ($item in $cur) { [void]$queue.Enqueue($item) }')

# 2) Parameter binding must not require [int] (that throws on Int32[]).
$common = $common.Replace('[Parameter(Mandatory = $true)][int]$ProcessId', '[Parameter(Mandatory = $true)]$ProcessId')
$common = $common.Replace('param([int]$ProcessId)', 'param($ProcessId)')
$common = $common.Replace("        [int]`$ProcessId,", "        `$ProcessId,")

# 3) Insert PID helpers if this checkout predates them.
if ($common -notmatch 'function ConvertTo-GarayePid\b') {
    if ($common -match 'function Get-ListeningPids') {
        $common = $common.Replace('function Get-ListeningPids', $pidHelpers + "function Get-ListeningPids")
    }
    else {
        $common += "`r`n" + $pidHelpers
    }
}
elseif ($common -notmatch 'function Get-GarayePortListenerIds') {
    if ($common -match 'function Get-ListeningPids') {
        $common = $common.Replace('function Get-ListeningPids', "function Get-GarayePortListenerIds {`r`n    param([int]`$Port)`r`n    ConvertTo-GarayePidArray (Get-ListeningPids -Port `$Port)`r`n}`r`n`r`nfunction Get-ListeningPids")
    }
}

# 4) Coerce ProcessId at the start of Test-GarayeOurUvicorn if not already present.
if ($common -match 'function Test-GarayeOurUvicorn' -and $common -notmatch 'function Test-GarayeOurUvicorn[\s\S]{0,500}ConvertTo-GarayePid \$ProcessId') {
    $common = [regex]::Replace(
        $common,
        '(function Test-GarayeOurUvicorn\s*\{[\s\S]*?param\([\s\S]*?\)\s*)',
        {
            param($m)
            $m.Groups[1].Value + "`r`n    `$ProcessId = ConvertTo-GarayePid `$ProcessId`r`n    if (`$ProcessId -le 0) { return `$false }`r`n"
        },
        1
    )
}

Save-Text $commonPath $common
Write-Host "Patched $commonPath"

function Patch-ListenerAssignments([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    $text = Get-Text $Path
    $original = $text
    $text = $text.Replace('$listeners = @(Get-ListeningPids -Port $Port)', $flatten)
    $text = $text.Replace('$listeners = ConvertTo-GarayePidArray (Get-ListeningPids -Port $Port)', $flatten)
    $text = $text.Replace('$listenPids = @(Get-ListeningPids -Port $Port)', ($flatten.Replace('$listeners', '$listenPids')))
    $text = $text.Replace('$listenPids = ConvertTo-GarayePidArray (Get-ListeningPids -Port $Port)', ($flatten.Replace('$listeners', '$listenPids')))
    $text = $text.Replace('$pids = ConvertTo-GarayePidArray (Get-ListeningPids -Port $Port)', ($flatten.Replace('$listeners', '$pids')))
    $text = $text.Replace('$pids = @(Get-ListeningPids -Port $Port)', ($flatten.Replace('$listeners', '$pids')))
    if ($text -ne $original) {
        Save-Text $Path $text
        Write-Host "Patched $Path"
    }
    else {
        Write-Host "No listener-assignment changes needed in $Path"
    }
}

Patch-ListenerAssignments $supPath
Patch-ListenerAssignments $statusPath
Patch-ListenerAssignments $stopPath

Write-Host ""
Write-Host "Hotfix applied. Start the supervisor again:"
Write-Host "  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\backend_supervisor.ps1 -HostAddress 127.0.0.1 -Port 8000"
Write-Host "You should see 'Started uvicorn' and then:"
Write-Host "  curl.exe http://127.0.0.1:8000/health"
