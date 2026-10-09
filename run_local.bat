@echo off
REM Start Water Storage Site Finder at http://localhost:8000  (close this window to stop it)
cd /d %~dp0
start "" cmd /c "timeout /t 6 >nul & start http://localhost:8000"
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
) else (
  call conda run -n damfinder --no-capture-output python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
)
pause
