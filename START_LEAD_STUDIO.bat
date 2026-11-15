@echo off
REM Lead Studio starts in its own window. No console, no browser tab.
REM To watch the log or debug a failed start, use START_CONSOLE.bat instead.
cd /d "%~dp0"
start "" pythonw leadstudio.py %*
exit /b 0
