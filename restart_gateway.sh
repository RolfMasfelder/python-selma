#!/usr/bin/env bash
set -euo pipefail

GATEWAY_LOG="gateway.log"

# Kill processes listening on port 8000
PID=$(lsof -i :8000 -t 2>/dev/null || true)
if [ -n "$PID" ]; then
    echo "Stopping process on port 8000 (PID $PID)..."
    kill "$PID" 2>/dev/null || true
fi

# Kill any remaining gateway.py processes by name
PIDS=$(pgrep -f "gateway\.py" 2>/dev/null || true)
if [ -n "$PIDS" ]; then
    echo "Stopping gateway.py processes (PID $PIDS)..."
    echo "$PIDS" | xargs kill 2>/dev/null || true
fi

# Wait until port 8000 is free
for _ in $(seq 1 10); do
    lsof -i :8000 -t &>/dev/null || break
    sleep 0.5
done

echo "Starting gateway (log: $GATEWAY_LOG)..."
uv run gateway.py >> "$GATEWAY_LOG" 2>&1 &
echo "Gateway started (PID $!)."
