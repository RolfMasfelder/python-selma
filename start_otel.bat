@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat

echo Starting OpenTelemetry collector (Arize Phoenix)...
start "Arize Phoenix" /B phoenix serve

echo Waiting for Phoenix to start...
timeout /T 2 /NOBREAK >nul

echo Opening Phoenix UI in default browser...
start http://localhost:6006
