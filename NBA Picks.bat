@echo off
rem Double-click to open NBA Picks without a command prompt window.
rem Gets the latest code, and only reinstalls packages when requirements.txt changed
rem (running pip on every launch used to add 5-15 seconds).
cd /d "%~dp0"
if not exist logs mkdir logs
git pull --quiet --ff-only >nul 2>&1
fc /b requirements.txt logs\requirements.installed >nul 2>&1
if errorlevel 1 (
  python -m pip install --quiet -r requirements.txt >nul 2>&1 && copy /y requirements.txt logs\requirements.installed >nul
)
start "" pythonw app.py
