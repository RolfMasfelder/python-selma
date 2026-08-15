#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
source venv/bin/activate

GATEWAY_LOG="gateway.log"
PHOENIX_LOG="phoenix.log"

cleanup() {
    echo ""
    echo "Shutting down..."
    kill "$GATEWAY_PID" 2>/dev/null || true
    kill "$PHOENIX_PID" 2>/dev/null || true
    wait "$GATEWAY_PID" 2>/dev/null || true
    wait "$PHOENIX_PID" 2>/dev/null || true
    echo "Done."
}

trap cleanup EXIT INT TERM

# Kill any existing gateway processes before starting
PID=$(lsof -i :8000 -t 2>/dev/null || true)
if [ -n "$PID" ]; then
    echo "Stopping process on port 8000 (PID $PID)..."
    kill "$PID" 2>/dev/null || true
fi
PIDS=$(pgrep -f "selma\.gateway" 2>/dev/null || true)
if [ -n "$PIDS" ]; then
    echo "Stopping selma.gateway processes (PID $PIDS)..."
    echo "$PIDS" | xargs kill 2>/dev/null || true
fi
for _ in $(seq 1 10); do
    lsof -i :8000 -t &>/dev/null || break
    sleep 0.5
done

echo "Starting Phoenix (log: $PHOENIX_LOG, UI: http://localhost:6006)..."
phoenix serve > "$PHOENIX_LOG" 2>&1 &
PHOENIX_PID=$!

echo -n "Waiting for Phoenix..."
PHOENIX_OK=0
for _ in $(seq 1 30); do
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:6006 2>/dev/null | grep -qE "^[23]"; then
        PHOENIX_OK=1
        break
    fi
    echo -n "."
    sleep 1
done
echo ""
if [ "$PHOENIX_OK" -eq 1 ]; then
    echo "Phoenix is online at http://localhost:6006"
else
    echo "Warning: Phoenix did not respond after 30s — check $PHOENIX_LOG"
fi

echo "Starting gateway (log: $GATEWAY_LOG)..."
python -m selma.gateway > "$GATEWAY_LOG" 2>&1 &
GATEWAY_PID=$!

echo "Starting dashboard..."
streamlit run src/selma/dashboard.py
