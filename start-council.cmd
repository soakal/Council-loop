@echo off
REM ============================================================
REM  Council Loop launcher
REM  Opens Claude Code in a target PROJECT, with this plugin's own
REM  local copy loaded (--plugin-dir), so /goal, /council-cycle,
REM  /council-status and the council agents are all available --
REM  whether or not Council Loop is separately installed via a
REM  marketplace.
REM
REM  Double-click with no argument: uses the current directory as
REM  the project. Drag a project folder onto this file, or pass
REM  its path as an argument, to target that folder instead.
REM ============================================================

set "PLUGIN_DIR=%~dp0"
if "%PLUGIN_DIR:~-1%"=="\" set "PLUGIN_DIR=%PLUGIN_DIR:~0,-1%"
set "PROJECT_DIR=%~1"
if "%PROJECT_DIR%"=="" set "PROJECT_DIR=%CD%"

if not exist "%PROJECT_DIR%" (
  echo Project directory does not exist: %PROJECT_DIR%
  pause
  exit /b 1
)

cd /d "%PROJECT_DIR%"

set "TARGET_REPO="
for /f "usebackq delims=" %%T in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$cfg = Join-Path $PWD '.council\config.json'; $localPath = Join-Path $PWD '.council\config.local.json'; if (-not (Test-Path $cfg)) { Write-Output '(not set up yet -- run /goal to get started)' } else { $target = $null; $source = 'config.json'; try { $base = Get-Content $cfg -Raw | ConvertFrom-Json; $target = $base.target_repo } catch { $target = '<could not read config.json>' }; if (Test-Path $localPath) { try { $local = Get-Content $localPath -Raw | ConvertFrom-Json; if ($local.PSObject.Properties.Name -contains 'target_repo') { $target = $local.target_repo; $source = 'config.local.json' } } catch { $target = '<could not read config.local.json>'; $source = 'config.local.json' } }; Write-Output ($target + '  (from ' + $source + ')') }"`) do set "TARGET_REPO=%%T"
echo.
echo   Council Loop
echo   ------------
echo   Project : %PROJECT_DIR%
echo   Target  : %TARGET_REPO%
echo   Plugin  : %PLUGIN_DIR%
echo.
echo   Next:  /council-loop:goal ^<objective^>. Acceptance: ^<criteria^>
echo          /loop /council-loop:council-cycle
echo.

where claude >nul 2>&1
if errorlevel 1 (
  echo   [!] "claude" was not found on your PATH.
  echo       Install / open Claude Code, then run it here manually.
  echo.
  pause
  exit /b 1
)

claude --plugin-dir "%PLUGIN_DIR%"
