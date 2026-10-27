@echo off
rem update.bat — the FREE update (no credits, no questions). Task Scheduler runs this.
cd /d "%~dp0"
if not exist logs mkdir logs
echo ===== %date% %time% ===== >> logs\update.log
python daily.py update >> logs\update.log 2>&1
