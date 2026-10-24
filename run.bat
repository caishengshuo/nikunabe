@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=C:\Python314\python.exe"
set "PYW=C:\Python314\pythonw.exe"
if not exist "%PY%" set "PY=python"
if not exist "%PYW%" set "PYW=pythonw"

if /i "%~1"=="console" (
    "%PY%" -X utf8 main.py
    exit /b
)
if /i "%~1"=="selftest" (
    "%PY%" -X utf8 selftest.py --all
    pause
    exit /b
)

start "" "%PYW%" -X utf8 main.py
exit /b
