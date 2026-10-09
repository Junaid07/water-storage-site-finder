@echo off
REM Install any new packages after an update
cd /d %~dp0
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python -m pip install -r requirements.txt
) else (
  call conda run -n damfinder --no-capture-output python -m pip install -r requirements.txt
)
echo.
echo Update finished. Now run run_local.bat
pause
