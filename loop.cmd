@echo off
rem loop -- Council Loop CLI (Windows wrapper around scripts\loop.py). See: loop --help
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 "%~dp0scripts\loop.py" %*
) else (
  python "%~dp0scripts\loop.py" %*
)
exit /b %ERRORLEVEL%
