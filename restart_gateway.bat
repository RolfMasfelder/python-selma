@echo off
setlocal

echo Stopping gateway...
taskkill /FI "WINDOWTITLE eq Selma Gateway" /F >nul 2>&1

echo Starting gateway (log: gateway.log)...
start "Selma Gateway" cmd /C "uv run gateway.py >> gateway.log 2>&1"
echo Gateway started.
