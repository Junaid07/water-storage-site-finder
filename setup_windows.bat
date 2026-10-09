@echo off
REM One-time setup for Water Storage Site Finder on Windows.
cd /d %~dp0
where conda >nul 2>nul
if %errorlevel%==0 (
  echo [Water Storage Site Finder] Creating conda environment "damfinder" with Python 3.11 ...
  call conda create -y -n damfinder python=3.11
  call conda run -n damfinder --no-capture-output python -m pip install -r requirements.txt
) else (
  echo [Water Storage Site Finder] conda not found - creating a virtual environment in .venv ...
  python -m venv .venv
  call .venv\Scripts\python -m pip install --upgrade pip
  call .venv\Scripts\python -m pip install -r requirements.txt
)
echo.
echo Setup finished. Next: run check_sources.bat, then run_local.bat
pause
