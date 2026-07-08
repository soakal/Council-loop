@echo off
REM ============================================================
REM  Council Loop launcher
REM  Double-click this file to open Claude Code in this folder,
REM  so /goal, /council-cycle, /council-status and the council
REM  agents are all available. Works no matter where the folder
REM  lives (uses this script's own location).
REM ============================================================

cd /d "%~dp0"
set "TARGET_REPO="
for /f "usebackq delims=" %%T in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$cfg = Join-Path $PWD '.council\config.json'; $localPath = Join-Path $PWD '.council\config.local.json'; $target = $null; $source = 'config.json'; try { $base = Get-Content $cfg -Raw | ConvertFrom-Json; $target = $base.target_repo } catch { $target = '<could not read config.json>' }; if (Test-Path $localPath) { try { $local = Get-Content $localPath -Raw | ConvertFrom-Json; if ($local.PSObject.Properties.Name -contains 'target_repo') { $target = $local.target_repo; $source = 'config.local.json' } } catch { $target = '<could not read config.local.json>'; $source = 'config.local.json' } }; Write-Output ($target + '  (from ' + $source + ')')"`) do set "TARGET_REPO=%%T"
echo.
echo   Council Loop
echo   ------------
echo   Folder : %CD%
echo   Target : %TARGET_REPO%
echo.
echo   Next:  /goal ^<objective^>. Acceptance: ^<criteria^>
echo          /loop /council-cycle
echo.

where claude >nul 2>&1
if errorlevel 1 (
  echo   [!] "claude" was not found on your PATH.
  echo       Install / open Claude Code, then run it here manually.
  echo.
  pause
  exit /b 1
)

claude
