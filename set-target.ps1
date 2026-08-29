<#
  set-target.ps1 - point Council Loop, for a given PROJECT, at the repo you want it to
  work on, without hand-editing JSON.

  Writes PROJECT\.council\config.local.json (the gitignored per-machine override file;
  local wins over the tracked PROJECT\.council\config.json, which is never touched).
  PROJECT defaults to the current directory: Council Loop is a plugin whose state
  belongs to whichever project you're actually working in, never to wherever this
  script (or the plugin itself) happens to live.

  NOTE: keep this file plain ASCII. Non-ASCII characters (e.g. em dashes) inside
  double-quoted strings can be mis-decoded by Windows PowerShell 5.1's default
  Get-Content encoding when the .ps1 has no BOM, which truncates the string at a
  garbled quote character and breaks the whole script.

  Usage:
    .\set-target.ps1                                          # report cwd's effective target_repo
    .\set-target.ps1 "C:\path\to\repo"                        # set it, project = cwd
    .\set-target.ps1 "C:\path\to\repo" "C:\path\to\project"    # set it for a specific project
    .\set-target.ps1 .                                        # set it back to the project itself
#>
param(
  [Parameter(Position = 0)]
  [string]$Path,
  [Parameter(Position = 1)]
  [string]$ProjectDir
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($ProjectDir)) {
  $ProjectDir = (Get-Location).Path
}
if (-not (Test-Path $ProjectDir)) {
  Write-Error "Project directory does not exist: $ProjectDir"
  exit 1
}
$ProjectDir = (Resolve-Path $ProjectDir).Path

$cfgPath = Join-Path $ProjectDir '.council\config.json'
$localPath = Join-Path $ProjectDir '.council\config.local.json'

if (-not (Test-Path $cfgPath)) {
  Write-Error "No .council\config.json in $ProjectDir yet -- run /goal there first (it bootstraps one)."
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
  Write-Host "Project: $ProjectDir"
  Write-Host 'Usage: .\set-target.ps1 "C:\path\to\your\repo" ["C:\path\to\project"]   (or "." for the project itself)'
  exit 0
}

# Normalize: store an absolute path (so it means the same thing regardless of which
# directory a later command happens to run from), forward slashes are safest inside
# JSON. Mirrors set-target.sh's resolution rules exactly.
$trimmedPath = $Path.Trim()
if ($trimmedPath -eq '.') {
  $normalized = '.'
} elseif (Test-Path $Path -PathType Container) {
  $normalized = (Resolve-Path $Path).Path.Replace('\', '/')
} elseif ([System.IO.Path]::IsPathRooted($trimmedPath)) {
  $normalized = $trimmedPath.Replace('\', '/')
} else {
  $normalized = (Join-Path (Get-Location).Path $trimmedPath).Replace('\', '/')
}

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
Write-Host "target_repo set to: $normalized  (written to $localPath)"
