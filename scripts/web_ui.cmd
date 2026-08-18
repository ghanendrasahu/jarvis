@echo off
setlocal
cd /d "%~dp0\.."

start "JARVIS Web UI" cmd /c "if exist .venv\Scripts\uvicorn.exe ( .venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000 > data\web_ui.log 2>&1 ) else ( uvicorn app.main:app --host 127.0.0.1 --port 8000 > data\web_ui.log 2>&1 )"

timeout /t 6 /nobreak >nul
start "" "http://127.0.0.1:8000"

endlocal
