@echo off
rem Double-click to open NBA Picks without a command prompt window.
cd /d "%~dp0"
git pull --quiet --ff-only >nul 2>&1
python -m pip install --quiet -r requirements.txt >nul 2>&1
start "" pythonw app.py
