@echo off
chcp 65001 >nul
setlocal
rem ===========================================================
rem  RESCUE: bring back a game window stuck in minimized state.
rem  If the game runs as administrator, this MUST run elevated
rem  (a UAC prompt will appear).
rem ===========================================================
if /i "%~1"=="elevated" goto run

net session >nul 2>&1
if %errorlevel%==0 goto run

echo Requesting administrator rights (click Yes in the UAC prompt)...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -Verb RunAs"
exit /b

:run
set PY=C:\Users\gui\.workbuddy\binaries\python\envs\default\Scripts\python.exe
if not exist "%PY%" set PY=python
"%PY%" "%~dp0rescue_window.py" %2
if errorlevel 1 pause
