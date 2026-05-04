@echo off
setlocal
cd /d "%~dp0\.."

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m app.cli
) else (
  python -m app.cli
)

endlocal

