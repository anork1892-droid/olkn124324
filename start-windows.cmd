@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto missing
if not defined BOUNTYOS_MODEL set "BOUNTYOS_MODEL=qwen2.5-coder:3b"
if not defined BOUNTYOS_MODEL_TIMEOUT set "BOUNTYOS_MODEL_TIMEOUT=1800"
echo Starting BountyOS with a local model. Keep this window and Ollama open.
echo Close the dashboard tab whenever you like. Press Ctrl+C here to stop the app.
".venv\Scripts\python.exe" -m bountyos --open-browser
if errorlevel 1 goto failed
exit /b 0

:missing
echo Run install-windows.cmd first.
pause
exit /b 1

:failed
echo.
echo BountyOS stopped. Read the error above.
pause
exit /b 1
