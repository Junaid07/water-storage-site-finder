@echo off
REM Checks that all open-data services are reachable from this PC.
cd /d %~dp0
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python check_sources.py %*
) else (
  call conda run -n damfinder --no-capture-output python check_sources.py %*
)
pause
