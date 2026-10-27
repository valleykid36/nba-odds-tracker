@echo off
rem schedule_windows.bat — run the FREE update automatically every day (no credits).
rem   Double-click to turn it on (safe to run again).   To turn it off:  schedule_windows.bat off
rem Times are around when teams file injury reports, plus a late run that grades the night.
rem If the PC is asleep or off at a run time, it runs as soon as it can.
cd /d "%~dp0"
set "HERE=%~dp0"
for %%T in (1010 1310 1740 1910 2350) do schtasks /delete /tn "NBA Picks update %%T" /f >nul 2>&1
if /i "%1"=="off" (
  echo Daily update turned off.
  pause
  exit /b
)
for %%T in (10:10 13:10 17:40 19:10 23:50) do call :add %%T
echo.
echo Daily update is on: 10:10 AM, 1:10 PM, 5:40 PM, 7:10 PM and 11:50 PM.
echo Log: %HERE%logs\update.log
pause
exit /b

:add
set T=%1
set N=%T::=%
schtasks /create /tn "NBA Picks update %N%" /tr "\"%HERE%update.bat\"" /sc daily /st %T% /f >nul
powershell -NoProfile -Command "$t = Get-ScheduledTask -TaskName 'NBA Picks update %N%'; $t.Settings.StartWhenAvailable = $true; $t.Settings.DisallowStartIfOnBatteries = $false; Set-ScheduledTask -InputObject $t | Out-Null"
exit /b
