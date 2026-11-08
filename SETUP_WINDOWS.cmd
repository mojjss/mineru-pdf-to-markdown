@echo off
setlocal
cd /d "%~dp0"
echo Looking for a compatible Python installation...
if exist ".venv\Scripts\python.exe" goto install
set "converter_python="
for %%V in (3.12 3.11 3.13 3.14) do (
    if not defined converter_python (
        for /f "delims=" %%P in ('py -%%V -c "import sys, tkinter; print(sys.executable)" 2^>nul') do set "converter_python=%%P"
    )
)
if not defined converter_python (
    for /f "delims=" %%P in ('python -c "import sys, tkinter; assert (3, 11) <= sys.version_info[:2] < (3, 15); print(sys.executable)" 2^>nul') do set "converter_python=%%P"
)
if not defined converter_python goto missing_python
echo Using "%converter_python%"
"%converter_python%" -m venv .venv
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -c "import sys, tkinter; assert (3, 11) <= sys.version_info[:2] < (3, 15); print(sys.version)"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Setup complete. Opening the converter...
".venv\Scripts\python.exe" converter.py
if errorlevel 1 goto failed
exit /b 0
:missing_python
echo No Python 3.11-3.14 installation with Tkinter was found.
echo Install Python 3.12 for Windows from https://www.python.org/downloads/windows/
echo Include the Python launcher and Tcl/Tk, then run this file again.
echo If you use Anaconda, you can also run this file from its activated prompt.
pause
exit /b 1
:failed
echo Setup or launch failed. Read the message above.
pause
exit /b 1
