@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   Find and restore windows hidden by WindowHide
echo   Run this as Administrator (right click -> Run as administrator)
echo ============================================
echo.
".venv\Scripts\python.exe" "tests\rescue_hidden.py" %*
echo.
pause
