@echo off
rem make_shortcut.bat - double-click once to put an "NBA Picks" icon on the Desktop and Start menu.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_shortcut.ps1"
pause
