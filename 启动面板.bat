@echo off
cd /d "%~dp0"
echo ========================================
echo   MediaExport Panel
echo   Starting...
echo ========================================
echo.
start "" pythonw launcher.py
exit
