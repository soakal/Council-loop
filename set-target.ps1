<#
  set-target.ps1 — point Council Loop at the repo you want it to work on,
  without hand-editing JSON.

  Usage:
    .\set-target.ps1                       # show the current target_repo
    .\set-target.ps1 "C:\path\to\repo"     # set target_repo to that path
    .\set-target.ps1 .                     # set it back to this folder
#>
param(
  [Parameter(Position = 0)]
  [string]$Path
)

$ErrorActionPreference = 'Stop'
$cfgPath = Join-Path $PSScriptRoot '.council\config.json'

if (-not (Test-Path $cfgPath)) {
  Write-Error "Config not found: $cfgPath"
  exit 1
}

$content = Get-Content $cfgPath -Raw

# No argument -> just report the current value.
if ([string]::IsNullOrWhiteSpace($Path)) {
  if ($content -match '"target_repo"\s*:\s*"([^"]*)"') {
    Write-Host "Current target_repo: $($Matches[1])"
  } else {
    Write-Warning "Could not find target_repo in $cfgPath"
  }
  Write-Host 'Usage: .\set-target.ps1 "C:\path\to\your\repo"   (or "." for this folder)'
  exit 0
}

# Normalize: forward slashes are safest inside JSON.
$normalized = $Path.Trim().Replace('\', '/')

if ($normalized -ne '.' -and -not (Test-Path $Path)) {
  Write-Warning "That path doesn't exist yet: $Path  (setting it anyway)"
}

# Replace only the target_repo value; leave the rest of the file untouched.
$replacement = '${1}' + $normalized + '${2}'
$new = $content -replace '("target_repo"\s*:\s*")[^"]*(")', $replacement

if ($new -eq $content) {
  Write-Warning "target_repo line not found — no change made."
  exit 1
}

Set-Content -Path $cfgPath -Value $new -NoNewline -Encoding UTF8
Write-Host "target_repo set to: $normalized"
