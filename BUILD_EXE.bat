@echo off
REM Build the packaged app as a FOLDER (dist\LeadStudioApp) and install it here.
REM
REM Why not a single .exe: PyInstaller's --onefile builds unpack themselves into %TEMP%
REM on every start, and on this machine AVG + ESET block that ("could not create
REM temporary directory", "failed to start embedded python interpreter"). A folder
REM build needs no unpacking, so it starts normally and opens faster too.
REM
REM Result: .\LeadStudioApp\LeadStudioApp.exe  - start it with LEAD_STUDIO_APP.vbs
title Build Lead Studio
cd /d "%~dp0"

where python >nul 2>nul || (
  echo Python was not found. Install it from https://www.python.org/downloads/ first.
  pause
  exit /b 1
)

python -c "import PyInstaller" >nul 2>nul || (
  echo Installing PyInstaller...
  python -m pip install --upgrade pyinstaller || (
    echo Could not install PyInstaller. Check your internet connection.
    pause
    exit /b 1
  )
)

python -m PyInstaller --noconfirm --clean --onedir ^
  --name LeadStudioApp ^
  --noconsole ^
  --add-data "ui;ui" ^
  --add-data "samples;samples" ^
  --collect-submodules engine ^
  --exclude-module pandas --exclude-module pyarrow --exclude-module numpy --exclude-module duckdb --exclude-module matplotlib ^
  leadstudio.py

if not exist "dist\LeadStudioApp\LeadStudioApp.exe" (
  echo.
  echo   Build failed - see the messages above.
  pause
  exit /b 1
)

echo.
echo   Installing into .\LeadStudioApp ...
if exist "LeadStudioApp" rmdir /s /q "LeadStudioApp"
xcopy /e /i /q /y "dist\LeadStudioApp" "LeadStudioApp" >nul

echo.
echo   Done. Start it with LEAD_STUDIO_APP.vbs (no console, tray icon).
echo   Your data folder is reused - your leads, offers and videos stay where they are.
echo.
pause
