@echo off
setlocal
cd /d "%~dp0\.."

set "JARVIS_AUTO_START_VOICE=false"
if /I "%~1"=="voice" set "JARVIS_AUTO_START_VOICE=true"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m app.gui
) else (
  python -m app.gui
)

endlocal
