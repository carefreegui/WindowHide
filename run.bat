@echo off
rem ===========================================================
rem  WindowHide launcher
rem  Picks the first interpreter that can actually import PySide6:
rem    1) .venv\Scripts\pythonw.exe in this folder (recommended)
rem    2) pythonw.exe on PATH
rem    3) pyw.exe launcher (py -3 / latest)
rem  A plain "pythonw.exe on PATH" is NOT enough: it may be a bare
rem  Python without PySide6, which fails silently (no console).
rem ===========================================================
setlocal
cd /d %~dp0

set "PYW="
set "PYARGS="

rem ---- 1) local venv -------------------------------------------
if exist "%~dp0.venv\Scripts\pythonw.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "import PySide6" >nul 2>&1
    if not errorlevel 1 set "PYW=%~dp0.venv\Scripts\pythonw.exe"
)

rem ---- 2) pythonw.exe on PATH, but only if it has PySide6 ------
if not defined PYW (
    for /f "delims=" %%I in ('where pythonw.exe 2^>nul') do (
        if not defined PYW (
            "%%I" -c "import PySide6" >nul 2>&1
            if not errorlevel 1 set "PYW=%%I"
        )
    )
)

rem ---- 3) py launcher ------------------------------------------
if not defined PYW (
    for /f "delims=" %%I in ('where pyw.exe 2^>nul') do (
        if not defined PYW (
            "%%I" -3 -c "import PySide6" >nul 2>&1
            if not errorlevel 1 (
                set "PYW=%%I"
                set "PYARGS=-3"
            )
        )
    )
)

if not defined PYW goto nopython

start "" "%PYW%" %PYARGS% "%~dp0run.pyw"
exit /b 0

:nopython
echo.
echo   [ERROR] No Python with PySide6 was found.
echo.
echo   1. Install Python 3.10+ (check "Add python.exe to PATH")
echo   2. Install dependencies:
echo          pip install -r requirements.txt
echo.
echo   Or use a project-local virtual environment (recommended):
echo          python -m venv .venv
echo          .venv\Scripts\pip install -r requirements.txt
echo      then run this file again.
echo.
pause
exit /b 1
