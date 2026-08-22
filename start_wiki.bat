@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0configure_api_key.ps1"
if errorlevel 1 (
  echo API key setup failed. Please close this window and try again.
  pause
  exit /b 1
)
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8765/'"
python server.py
pause
