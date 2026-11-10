# Compatibility wrapper (PLAN.md D2): the driver logic now lives in scripts\loop.py (`loop run`).
# Usage: .\run-loop.ps1 [-TargetDir <path>] [-MaxIterations <n>]
#   -TargetDir      Project to drive (default: current directory); must have run `loop init`.
#   -MaxIterations  Optional; passed as --max-sessions, which can only LOWER loop.max_sessions.
param([string]$TargetDir = ".", [int]$MaxIterations = 0)
if (-not (Test-Path $TargetDir)) { Write-Host "run-loop.ps1: target directory does not exist: $TargetDir"; exit 1 }
$exe = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } elseif (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "python3" }
$loopArgs = @(if ($exe -eq "py") { "-3" }) + @((Join-Path $PSScriptRoot "scripts\loop.py"), "--project", (Resolve-Path $TargetDir).Path, "run")
if ($MaxIterations -gt 0) { $loopArgs += @("--max-sessions", "$MaxIterations") }
& $exe @loopArgs
if ($null -eq $LASTEXITCODE) { exit 1 }
exit $LASTEXITCODE
