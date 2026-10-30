@echo off
setlocal
cd /d "%~dp0"

rem Run the app from source, no exe build needed.
rem If nothing appears after a few seconds, run this in a cmd window to
rem see the error message:
rem     .venv\Scripts\python.exe app.py

set "VPYW=.venv\Scripts\pythonw.exe"
if not exist "%VPYW%" (
    echo No .venv found. Run build.bat once first, or install manually:
    echo     python -m pip install -r requirements.txt
    echo     python app.py
    echo.
    pause
    exit /b 1
)

start "" "%VPYW%" app.py
exit /b 0
