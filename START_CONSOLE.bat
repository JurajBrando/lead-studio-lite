@echo off
REM Debug launcher: keeps the console and its messages visible.
title Lead Studio Lite (console)
cd /d "%~dp0"
where python >nul 2>nul || (
  echo Python is not installed. Get it from https://www.python.org/downloads/
  pause
  exit /b 1
)
python leadstudio.py %*
echo.
echo The app stopped. Press any key to close.
pause >nul
