@echo off
setlocal
cd /d "%~dp0"

echo ==========================================================
echo   ESP32 HID Sender - Windows build
echo ==========================================================
echo.

rem ---- pick a Python launcher -------------------------------------------
set "PY=python"
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"

echo [1/6] Locating Python ...
%PY% -c "import sys; print('    Python ' + sys.version.split()[0] + ' @ ' + sys.executable)"
if errorlevel 1 goto no_python

rem ---- venv ------------------------------------------------------------
echo.
echo [2/6] Preparing virtual environment (.venv) ...
if not exist ".venv\Scripts\python.exe" (
    %PY% -m venv .venv
    if errorlevel 1 goto no_venv
)
set "VPY=.venv\Scripts\python.exe"

rem ---- dependencies ----------------------------------------------------
echo.
echo [3/6] Installing dependencies (bleak + pyinstaller) ...
"%VPY%" -m pip install --disable-pip-version-check -q --upgrade pip
"%VPY%" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto no_deps
"%VPY%" -m pip install --disable-pip-version-check -q pyinstaller
if errorlevel 1 goto no_deps

rem ---- smoke test: tkinter + bleak must import, layout must be sane ------
echo.
echo [4/6] Smoke test ...
"%VPY%" app.py --selftest
if errorlevel 1 goto no_deps

rem ---- bleak reaches Bluetooth through the winrt namespace packages.
rem      PyInstaller does not always pick those up, so collect them when
rem      present; on machines without them the flag is skipped.
echo.
echo [5/6] Checking the winrt layer ...
set "EXTRA="
"%VPY%" -c "import importlib.util as u, sys; sys.exit(0 if u.find_spec('winrt') else 1)" >nul 2>nul
if not errorlevel 1 set "EXTRA=--collect-all winrt"
if defined EXTRA echo     winrt found - it will be bundled
if not defined EXTRA echo     winrt not found - nothing extra to bundle

rem ---- build -----------------------------------------------------------
rem app.ico     -> exe icon shown in Explorer and the taskbar
rem appicon.png -> bundled as data so the Tk window icon matches
echo.
echo [6/6] Building the exe (1-3 minutes) ...
"%VPY%" -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name "ESP32 HID Sender" ^
    --icon "assets\app.ico" ^
    --add-data "assets\appicon.png;assets" ^
    --collect-submodules bleak %EXTRA% ^
    app.py
if errorlevel 1 goto build_fail

echo.
echo ==========================================================
echo   Done.
echo   exe: "%~dp0dist\ESP32 HID Sender.exe"
echo.
echo   That single file runs on any Windows 10/11 x64 machine.
echo   No Python, no pip, no admin rights needed.
echo ==========================================================
start "" "%~dp0dist"
goto end

:no_python
echo.
echo [ERROR] Python not found.
echo         Install Python 3.10+ from https://www.python.org/downloads/
echo         and tick "Add python.exe to PATH" during setup.
goto end

:no_venv
echo.
echo [ERROR] Could not create the virtual environment.
echo         Try: %PY% -m venv .venv
goto end

:no_deps
echo.
echo [ERROR] Dependency install or smoke test failed.
echo         Check the message above. Usually it is a network problem
echo         or a Python installed from the Microsoft Store.
goto end

:build_fail
echo.
echo [ERROR] PyInstaller failed. The last lines above show why.
echo         Workaround: just use run.bat, it runs the app from source.
goto end

:end
echo.
pause
exit /b 0
