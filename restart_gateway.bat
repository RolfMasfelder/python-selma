@echo off
setlocal

cd /d "%~dp0"
call venv\Scripts\activate.bat

echo Stopping gateway...
taskkill /FI "WINDOWTITLE eq Selma Gateway" /F >nul 2>&1

echo Starting gateway (log: gateway.log)...
start "Selma Gateway" cmd /C "python -m selma.gateway >> gateway.log 2>&1"
echo Gateway started.
