@echo off
setlocal
cd /d "%~dp0"
echo Installing BountyOS. This does not install or call paid AI services.
if exist ".venv\Scripts\python.exe" goto install
if exist ".venv" goto incompatible
py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>&1
if not errorlevel 1 goto launcher
python -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>&1
if errorlevel 1 goto missing
python -m venv .venv
if errorlevel 1 goto failed
goto install

:launcher
py -3 -m venv .venv
if errorlevel 1 goto failed

:install
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.lock
if errorlevel 1 goto failed
".venv\Scripts\python.exe" scripts\configure-windows.py
if errorlevel 1 goto failed
echo.
echo Installation complete. Double-click start-windows.cmd to open BountyOS.
echo Local model: run "ollama pull qwen2.5-coder:3b" in Terminal first.
pause
exit /b 0

:missing
echo Python 3.11 or newer was not found. Install Python for Windows with the launcher.
echo Close this window and run install-windows.cmd again after installation.
pause
exit /b 1

:incompatible
echo This folder contains an incompatible .venv. Extract a fresh copy of BountyOS.
echo Your existing files have not been deleted.
pause
exit /b 1

:failed
echo.
echo Installation did not finish. Read the error above; do not continue to Start yet.
pause
exit /b 1
