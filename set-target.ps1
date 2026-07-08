<#
  set-target.ps1 - point Council Loop at the repo you want it to work on,
  without hand-editing JSON.

  Writes to the gitignored per-machine override file .council\config.local.json
  (local wins over the tracked .council\config.json), so config.json itself is
  never touched.

  NOTE: keep this file plain ASCII. Non-ASCII characters (e.g. em dashes) inside
  double-quoted strings can be mis-decoded by Windows PowerShell 5.1's default
  Get-Content encoding when the .ps1 has no BOM, which truncates the string at a
  garbled quote character and breaks the whole script.

  Usage:
    .\set-target.ps1                       # show the effective target_repo
    .\set-target.ps1 "C:\path\to\repo"     # set target_repo to that path
    .\set-target.ps1 .                     # set it back to this folder
#>
param(
  [Parameter(Position = 0)]
  [string]$Path
)

$ErrorActionPreference = 'Stop'
$cfgPath = Join-Path $PSScriptRoot '.council\config.json'
$localPath = Join-Path $PSScriptRoot '.council\config.local.json'

if (-not (Test-Path $cfgPath)) {
  Write-Error "Config not found: $cfgPath"
  exit 1
}

# No argument -> report the effective target_repo (local override wins).
if ([string]::IsNullOrWhiteSpace($Path)) {
  $effective = $null
  $source = $null

  if (Test-Path $localPath) {
    try {
      $local = Get-Content $localPath -Raw | ConvertFrom-Json
      if ($local.PSObject.Properties.Name -contains 'target_repo') {
        $effective = $local.target_repo
        $source = 'config.local.json override'
      }
    } catch {
      Write-Warning "Could not parse $localPath - ignoring it for this report."
    }
  }

  if (-not $effective) {
    $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
    $effective = $cfg.target_repo
    $source = 'config.json'
  }

  if ($effective) {
    Write-Host "Current target_repo: $effective  (from $source)"
  } else {
    Write-Warning "Could not find target_repo in $cfgPath or $localPath"
  }
  Write-Host 'Usage: .\set-target.ps1 "C:\path\to\your\repo"   (or "." for this folder)'
  exit 0
}

# Normalize: forward slashes are safest inside JSON.
$normalized = $Path.Trim().Replace('\', '/')

if ($normalized -ne '.' -and -not (Test-Path $Path)) {
  Write-Warning "That path doesn't exist yet: $Path  (setting it anyway)"
} elseif ($normalized -ne '.') {
  git -C $Path rev-parse --git-dir *> $null
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "That target is not a git repository yet: $Path"
  }
}

# Load (or start) the local override object, then set/overwrite target_repo.
if (Test-Path $localPath) {
  try {
    $localObj = Get-Content $localPath -Raw | ConvertFrom-Json
  } catch {
    Write-Warning "Could not parse existing $localPath - recreating it."
    $localObj = [PSCustomObject]@{}
  }
} else {
  $localObj = [PSCustomObject]@{}
}

if ($localObj.PSObject.Properties.Name -contains 'target_repo') {
  $localObj.target_repo = $normalized
} else {
  $localObj | Add-Member -NotePropertyName 'target_repo' -NotePropertyValue $normalized
}

$json = $localObj | ConvertTo-Json -Depth 10

# Write UTF-8 without BOM (Set-Content -Encoding UTF8 adds a BOM on PowerShell 5.1,
# which breaks strict JSON parsers).
[System.IO.File]::WriteAllText($localPath, $json, [System.Text.UTF8Encoding]::new($false))
Write-Host "target_repo set to: $normalized  (written to .council\config.local.json)"
