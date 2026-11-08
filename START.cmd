@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run SETUP_WINDOWS.cmd first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" converter.py
if errorlevel 1 pause
