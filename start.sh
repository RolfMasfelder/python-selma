#!/usr/bin/env bash
# start.sh — KANONISCHE VERSION (Selma-Repo, MAIN + alle neuen Projekte)
#
# Eine einzige Quelle: create_new_selma_project.sh KOPiert diese Datei
# in jedes neue Projekt — MAIN und Projekte können nicht mehr
# auseinanderlaufen. (Nur die kill-all-Wartlogik liegt separat in
# kill_all_gateways.sh — die wird NICHT kopiert.)
#
# Startet:  1. Phoenix (GEMEINSAM: UI :6006, OTLP :4317)
#               erreichbar -> fertig; sonst -> starten (Kennzeichen
#               SELMA_PHOENIX_OWNER=1) + warten, bis er antwortet.
#           2. Selma-Gateway   (Port aus .selma/selma.json)
#           3. Streamlit       (PORT via STREAMLIT_PORT, Default 8501)
#
# Beim Stop (Reihenfolge zwingend: ERST eigenes Gateway):
#   * Laeuft noch irgendein selma.gateway?  -> Phoenix laeuft weiter.
#   * Kein selma.gateway mehr?              -> genau die mit Kennzeichen
#     (SELMA_PHOENIX_OWNER=1) markierten Phoenix beenden.
#     Manuell gestartete Phoenix bleiben NIEMALS unangetastet.
#
# Env:
#   STREAMLIT_PORT     Default 8501
#   PHOENIX_PORT       Default 6006
set -euo pipefail

cd "$(dirname "$0")"
source venv/bin/activate

PROJECT_DIR="$(pwd)"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
PHOENIX_PORT="${PHOENIX_PORT:-6006}"
STREAMLIT_PORT="${STREAMLIT_PORT:-8501}"
PHOENIX_OWNER_ENV="SELMA_PHOENIX_OWNER"

GATEWAY_PORT="$("$PROJECT_DIR/venv/bin/python" -c "
import json
print(json.load(open('${PROJECT_DIR}/.selma/selma.json')).get('channels', {}).get('webchat', {}).get('port', 8000))
")"

GATEWAY_PID=""
STREAMLIT_PID=""
CLEANUP_RAN=0

GATEWAY_LOG="gateway.log"
PHOENIX_LOG="phoenix.log"
STREAMLIT_LOG="streamlit.log"


# Laeuft noch EINE Selma-Gateway-Instanz (irgendwelches Projekt)?
# Eigene Instanz muss zu diesem Zeitpunkt bereits beendet sein
# (cleanup killt sie vorher), sonst waere sie im Treffer enthalten.
selma_gateway_still_running() {
    local d
    for d in /proc/[0-9]*/cmdline; do
        tr '\0' ' ' < "$d" 2>/dev/null | grep -q "selma\.gateway" && return 0
    done
    return 1
}

# PID des Phoenix-Prozesses auf PHOENIX_PORT (leer, falls keiner)
phoenix_pid() {
    command -v lsof >/dev/null 2>&1 || return 0
    lsof -ti ":${PHOENIX_PORT}" -sTCP:LISTEN 2>/dev/null | head -n 1 2>/dev/null
    return 0
}

# Hat der Phoenix-Prozess das Owner-Kennzeichen (von uns gestartet)?
phoenix_is_managed() {
    local pid; pid="$(phoenix_pid)"
    [ -n "$pid" ] || return 1
    tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep -q "^${PHOENIX_OWNER_ENV}=1$" || return 1
    return 0
}

ensure_phoenix() {
    local url="http://127.0.0.1:${PHOENIX_PORT}"
    if curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null | grep -qE "^[23]"; then
        echo "  → Phoenix erreichbar unter ${url} — nutze ich einfach."
        return 0
    fi
    echo "  → Phoenix nicht erreichbar — starte Instanz (log: $PHOENIX_LOG)…"
    env "${PHOENIX_OWNER_ENV}=1" phoenix serve --port "$PHOENIX_PORT" > "$PHOENIX_LOG" 2>&1 &
    local i ok=0
    for i in $(seq 1 30); do
        curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null | grep -qE "^[23]" && { ok=1; break; }
        sleep 1
    done
    if [ "$ok" -eq 1 ]; then
        echo "  → Phoenix online unter ${url}"
    else
        echo "  → WARNUNG: Phoenix antwortet nicht (30 s) — siehe $PHOENIX_LOG. Gateway läuft trotzdem (Tracing ist optional)!"
    fi
}

stop_phoenix_if_last() {
    if selma_gateway_still_running; then
        echo "  → Andere Selma-Gateways laufen noch — Phoenix läuft weiter."
        return 0
    fi
    local pid; pid="$(phoenix_pid)"
    if [ -z "$pid" ]; then
        echo "  → Kein Phoenix-Prozess auf :$PHOENIX_PORT gefunden — nichts zu tun."
        return 0
    fi
    if phoenix_is_managed; then
        echo "  → Kein Gateway mehr — beende das (gekennzeichnete) Phoenix (PID $pid)."
        kill "$pid" 2>/dev/null || true
    else
        echo "  → Phoenix läuft, ist aber NICHT von einem Skript gestartet — lasse ich laufen."
    fi
}

cleanup() {
    [ "$CLEANUP_RAN" -eq 1 ] && return 0
    CLEANUP_RAN=1
    echo ""
    echo "Stoppe Selma-Projekt: $PROJECT_NAME"
    if [ -n "${GATEWAY_PID:-}" ];   then kill "$GATEWAY_PID"   2>/dev/null || true; wait "$GATEWAY_PID"   2>/dev/null || true; fi
    if [ -n "${STREAMLIT_PID:-}" ]; then kill "$STREAMLIT_PID" 2>/dev/null || true; wait "$STREAMLIT_PID" 2>/dev/null || true; fi
    stop_phoenix_if_last
    echo "Fertig."
}
trap cleanup EXIT INT TERM

echo "=== Starte Selma-Projekt: $PROJECT_NAME ==="
echo "    Gateway-Port:   :$GATEWAY_PORT"
echo "    Streamlit-Port: :$STREAMLIT_PORT"
echo "    Phoenix:        :$PHOENIX_PORT  (gemeinsam, Kennzeichen: ${PHOENIX_OWNER_ENV}=1)"
echo ""

ensure_phoenix
echo ""
echo "  → Gateway starten (log: $GATEWAY_LOG)…"
python -m selma.gateway > "$GATEWAY_LOG" 2>&1 &
GATEWAY_PID=$!
echo "    Gateway PID: $GATEWAY_PID"
echo "  → Streamlit starten (PORT :$STREAMLIT_PORT, log: $STREAMLIT_LOG)…"
streamlit run src/selma/dashboard.py --server.port="$STREAMLIT_PORT" --server.address=127.0.0.1 --server.headless=true gateway-url="http://127.0.0.1:${GATEWAY_PORT}/webchat/stream" \
    > "$STREAMLIT_LOG" 2>&1 &
STREAMLIT_PID=$!
echo "    Streamlit PID: $STREAMLIT_PID"
echo ""
echo "=== Selma-Projekt läuft ==="
echo "    Gateway/WebChat:    http://127.0.0.1:${GATEWAY_PORT}"
echo "    Streamlit:          http://127.0.0.1:${STREAMLIT_PORT}"
echo "    Phoenix:            http://127.0.0.1:${PHOENIX_PORT}"
echo "    Stoppen:            Ctrl-C  (eigenes Gateway + Streamlit enden, Phoenix nur wenn kein anderes Gateway mehr läuft)"
echo ""
wait "$GATEWAY_PID" 2>/dev/null || true
