param(
    [string]$TargetDir = ".",
    [int]$MaxIterations = 120
)

# Unattended driver for Windows -- loops `claude -p "/council-loop:council-cycle"` against a TARGET
# PROJECT until stop.flag appears or MaxIterations is hit, then (best-effort, never
# affecting this script's exit code) emits one Brain run-complete event and triggers
# NEXUS's council post-mortem via scripts\postmortem_payload.py.
#
# Council Loop is a plugin: this script ships alongside scripts\ (its own install
# location, $PluginDir below) but OPERATES ON a separate $TargetDir -- whatever project
# you're actually driving. The two are never assumed to be the same directory.
#
# Usage: .\run-loop.ps1 [-TargetDir <path>] [-MaxIterations <n>]
#   -TargetDir      Project to drive (default: current directory).
#   -MaxIterations  Cycle cap for this invocation (default: 120).

$env:CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS = "0"
$ErrorActionPreference = "Continue"

# This script's own directory -- where scripts\council_state.py and
# scripts\postmortem_payload.py live. Never used to locate .council\ state.
$PluginDir = $PSScriptRoot

if (-not (Test-Path $TargetDir)) {
    Write-Host "run-loop.ps1: target directory does not exist: $TargetDir"
    exit 1
}
$TargetDir = (Resolve-Path $TargetDir).Path
Set-Location $TargetDir

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logFile = Join-Path $TargetDir "run-loop-$timestamp.log"
$stopFlagPath = Join-Path $TargetDir ".council\state\stop.flag"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -Path $logFile -Value $line
}

Write-Log "=== Council Loop driver starting (max $MaxIterations cycles) ==="
Write-Log "Target project: $TargetDir"
Write-Log "Plugin location: $PluginDir"
Write-Log "Log file: $logFile"
Write-Log "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 (wait indefinitely for background tasks -- avoids the 600s kill that interrupted the first run)"

$runStart = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")

for ($i = 1; $i -le $MaxIterations; $i++) {
    if (Test-Path $stopFlagPath) {
        Write-Log "stop.flag present before cycle $i -- halting."
        Write-Log ("stop.flag contents: " + (Get-Content $stopFlagPath -Raw))
        break
    }

    Write-Log "--- Starting cycle $i ---"
    $output = & claude -p "/council-loop:council-cycle" --plugin-dir $PluginDir 2>&1 | Out-String
    Write-Log $output

    if (Test-Path $stopFlagPath) {
        Write-Log "stop.flag written during cycle $i -- halting."
        Write-Log ("stop.flag contents: " + (Get-Content $stopFlagPath -Raw))
        break
    }
}

# Best-effort Brain event loopback: ONE event per driver run (never per-cycle),
# emitted here because every exit path (pre-cycle stop.flag, post-cycle
# stop.flag, ceiling exhaustion) converges at this single point after the
# loop. Never throws -- a missing python3, a down/unreachable Brain server,
# brain_events.enabled=false, or an empty run-summary (nothing recorded this
# run) must never change this driver's exit behavior or exit code.
try {
    $effectiveConfigJson = & python3 (Join-Path $PluginDir "scripts\council_state.py") --root $TargetDir effective-config 2>$null
    if ($LASTEXITCODE -eq 0 -and $effectiveConfigJson) {
        $config = ($effectiveConfigJson | Out-String) | ConvertFrom-Json
        $brainEvents = $config.brain_events
        if ($brainEvents -and $brainEvents.enabled) {
            $summaryRaw = & python3 (Join-Path $PluginDir "scripts\council_state.py") --root $TargetDir run-summary --since $runStart 2>$null
            $summaryText = (@($summaryRaw) -join "`n").Trim()
            if ($summaryText) {
                $summaryLines = $summaryText -split "`r?`n"
                $goalLine = ($summaryLines | Where-Object { $_ -like "Goal:*" } | Select-Object -First 1) -replace "^Goal:\s*", ""
                $goalWords = (($goalLine -split '\s+') | Select-Object -First 6) -join " "
                $cyclesLine = ($summaryLines | Where-Object { $_ -like "Cycles run:*" } | Select-Object -First 1) -replace "^Cycles run:\s*", ""
                $title = "Council run: $goalWords ($cyclesLine cycles)"

                $nowUtc = (Get-Date).ToUniversalTime()
                $whenIso = $nowUtc.ToString("yyyy-MM-ddTHH:mm:ssZ")
                $fileTs = $nowUtc.ToString("yyyyMMddTHHmmssZ")

                $content = @"
# Event: $title

- Source: council-loop
- Type: council.run-complete
- When: $whenIso

$summaryText

Powered by CwiAI
"@
                $bodyJson = @{ content = $content; filename = "event-council-loop-run-complete-$fileTs.md" } | ConvertTo-Json
                Invoke-RestMethod -Method Post -Uri "$($brainEvents.url)/raw" -ContentType "application/json" -TimeoutSec 5 -Body $bodyJson | Out-Null
                Write-Log "Brain event emitted: event-council-loop-run-complete-$fileTs.md"
            }
        }
    }
} catch {
    Write-Log "brain event emit skipped: $_"
}

# Best-effort NEXUS council post-mortem trigger: ONE call per driver run, here
# because /goal archives .council/state/history.jsonl into a timestamped
# folder on the next session -- NEXUS must read this session's history before
# that happens. Separate try/catch from the Brain block above on purpose: a
# down Brain server must not skip this. Delegates entirely to
# scripts\postmortem_payload.py (stdlib Python, runs identically on Windows)
# so both drivers share one payload contract instead of drifting -- see that
# script's own docstring for the raw-git-data shape NEXUS now expects. Never
# throws; never changes this driver's exit code.
try {
    $postmortemOutput = (& python3 (Join-Path $PluginDir "scripts\postmortem_payload.py") --root $TargetDir 2>&1 | Out-String).Trim()
    Write-Log $postmortemOutput
} catch {
    Write-Log "council post-mortem skipped: $_"
}

$cyclesRun = [Math]::Min($i, $MaxIterations)
Write-Log "=== Council Loop driver ended (ran up to $cyclesRun of $MaxIterations cycles) ==="
Write-Log "Check $TargetDir\.council\state\history.jsonl for the full cycle-by-cycle record."
