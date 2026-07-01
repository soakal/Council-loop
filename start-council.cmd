@echo off
REM ============================================================
REM  Council Loop launcher
REM  Double-click this file to open Claude Code in this folder,
REM  so /goal, /council-cycle, /council-status and the council
REM  agents are all available. Works no matter where the folder
REM  lives (uses this script's own location).
REM ============================================================

cd /d "%~dp0"
echo.
echo   Council Loop
echo   ------------
echo   Folder : %CD%
echo   Target : (see .council\config.json  -^>  target_repo)
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
