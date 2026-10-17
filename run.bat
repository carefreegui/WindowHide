@echo off
rem WindowHide 启动脚本(使用隔离 venv 的 Python)
cd /d %~dp0
set PY=%LOCALAPPDATA%\..\..\..\Users\gui\.workbuddy\binaries\python\envs\default\Scripts\python.exe
if not exist "%PY%" set PY=C:\Users\gui\.workbuddy\binaries\python\envs\default\Scripts\python.exe
start "" "%PY%" "%~dp0run.pyw"
