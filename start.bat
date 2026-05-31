@echo off
setlocal

echo Starting Phoenix (UI: http://localhost:6006)...
start "Selma Phoenix" cmd /C "uv run phoenix serve > phoenix.log 2>&1"

echo Starting gateway (log: gateway.log)...
start "Selma Gateway" cmd /C "uv run gateway.py > gateway.log 2>&1"

echo Starting dashboard (close this window or press Ctrl+C to stop)...
uv run streamlit run dashboard.py

echo.
echo Shutting down...
taskkill /FI "WINDOWTITLE eq Selma Gateway" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Selma Phoenix" /F >nul 2>&1
echo Done.
