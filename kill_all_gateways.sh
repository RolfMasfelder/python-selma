#!/usr/bin/env bash
# kill_all_gateways.sh — WART-WERKZEUG (Selma-MAIN, NICHT Teil eines Projekts)
#
# Beendet ALLE laufenden Selma-Gateways + ihre Streamlit-Instanzen,
# unabhängig von Projekt. Nur für Notfall-/Aufräum-Fälle gedacht
# (z. B. Prozesse hängen, Ports belegt). Wird NICHT von
# create_new_selma_project.sh kopiert — normale Projekte/MAIN
# verwenden ./start.sh, das nur ihr Eigenes stoppt.
#
# Logik entspricht der ALTEN MAIN-start.sh (vor v5-Kopieren):
#   * Prozess auf Port 8000 (oder GATEWAY_PORT) killen
#   * pgrep -f "selma.gateway" -> alle killen
#   * Streamlit (Streamlit-Port, Default 8501) killen
#   * Phoenix wird NICHT angefasst (selbständig; Managed-Phoenix
#     stoppt ohnehin per Projekt-Cleanup).
#
# Env: GATEWAY_PORT (Default 8000), STREAMLIT_PORT (Default 8501)
set -uo pipefail

GATEWAY_PORT="${GATEWAY_PORT:-8000}"
STREAMLIT_PORT="${STREAMLIT_PORT:-8501}"

echo "=== Stoppe ALLE Selma-Gateways + Streamlit (Wart-Modus) ==="

# 1. Gateway auf Main-Port
PID=$(lsof -i :"$GATEWAY_PORT" -t 2>/dev/null || true)
if [ -n "$PID" ]; then
    echo "  → Stoppe Prozess auf Port $GATEWAY_PORT (PID $PID)…"
    kill "$PID" 2>/dev/null || true
fi

# 2. ALLE selma.gateway-Prozesse (alle Projekte)
PIDS=$(pgrep -f "selma\.gateway" 2>/dev/null || true)
if [ -n "$PIDS" ]; then
    echo "  → Stoppe selma.gateway-Prozesse (PID $PIDS)…"
    echo "$PIDS" | xargs kill 2>/dev/null || true
fi

# 3. Streamlit-Frontends
SPID=$(lsof -i :"$STREAMLIT_PORT" -t 2>/dev/null || true)
if [ -n "$SPID" ]; then
    echo "  → Stoppe Streamlit auf Port $STREAMLIT_PORT (PID $SPID)…"
    kill "$SPID" 2>/dev/null || true
fi

# 4. Warten, bis Gateway-Port frei ist
for _ in $(seq 1 10); do
    lsof -i :"$GATEWAY_PORT" -t &>/dev/null || break
    sleep 0.5
done

REMAIN=$(pgrep -f "selma\.gateway" 2>/dev/null || true)
if [ -z "$REMAIN" ]; then
    echo "  → Alle selma.gateway-Prozesse beendet."
else
    echo "  → WARNUNG: noch vorhanden: $REMAIN (starker Kill mit kill -9?)"
fi

echo "Fertig. Phoenix wurde NICHT angefasst (läuft weiter)."
