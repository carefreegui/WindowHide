@echo off
rem ===========================================================
rem  RESCUE: bring back a window stuck in "minimized and
rem  unreachable" state (e.g. a fullscreen game minimized by
rem  WindowHide before it could be hidden).
rem
rem  If the target runs as administrator, this script MUST run
rem  elevated - it re-launches itself and shows a UAC prompt.
rem ===========================================================
setlocal
if /i "%~1"=="elevated" goto run

net session >nul 2>&1
if %errorlevel%==0 goto run

echo Requesting administrator rights (click Yes in the UAC prompt)...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList 'elevated' -Verb RunAs"
exit /b

:run
set "PY="
if exist "%~dp0..\.venv\Scripts\python.exe" set "PY=%~dp0..\.venv\Scripts\python.exe"
if not defined PY for /f "delims=" %%I in ('where python.exe 2^>nul') do if not defined PY set "PY=%%I"
if not defined PY for /f "delims=" %%I in ('where py.exe 2^>nul') do if not defined PY (
    set "PY=%%I"
    set "PYARGS=-3"
)

if not defined PY (
    echo   [ERROR] python.exe was not found.
    pause
    exit /b 1
)

"%PY%" %PYARGS% "%~dp0rescue_window.py" %2
if errorlevel 1 pause
