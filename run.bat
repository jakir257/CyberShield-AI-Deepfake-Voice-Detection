@echo off
setlocal
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe py -3 -m venv .venv
if not exist .venv\Scripts\python.exe python -m venv .venv
if not exist .venv\Scripts\python.exe goto :fail

rem re-run every launch: pip is a no-op when satisfied and self-heals a partial install
.venv\Scripts\python -m pip install -q fastapi "uvicorn[standard]" websockets numpy faster-whisper
if errorlevel 1 goto :fail

.venv\Scripts\python code.py
echo.
pause
exit /b %errorlevel%

:fail
echo.
echo Setup failed. Need Python 3.9+ on PATH.
pause
exit /b 1
