param(
    [int]$MaxIterations = 120
)

$env:CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS = "0"
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logFile = Join-Path $PSScriptRoot "run-loop-$timestamp.log"
$stopFlagPath = Join-Path $PSScriptRoot ".council\state\stop.flag"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -Path $logFile -Value $line
}

Write-Log "=== Council Loop driver starting (max $MaxIterations cycles) ==="
Write-Log "Log file: $logFile"
Write-Log "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 (wait indefinitely for background tasks -- avoids the 600s kill that interrupted the first run)"

for ($i = 1; $i -le $MaxIterations; $i++) {
    if (Test-Path $stopFlagPath) {
        Write-Log "stop.flag present before cycle $i -- halting."
        Write-Log ("stop.flag contents: " + (Get-Content $stopFlagPath -Raw))
        break
    }

    Write-Log "--- Starting cycle $i ---"
    $output = & claude -p "/council-cycle" 2>&1 | Out-String
    Write-Log $output

    if (Test-Path $stopFlagPath) {
        Write-Log "stop.flag written during cycle $i -- halting."
        Write-Log ("stop.flag contents: " + (Get-Content $stopFlagPath -Raw))
        break
    }
}

Write-Log "=== Council Loop driver ended (ran up to $i of $MaxIterations cycles) ==="
Write-Log "Check .council/state/history.jsonl for the full cycle-by-cycle record."
