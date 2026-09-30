#!/usr/bin/env bash
# create_new_selma_project.sh — legt NACHBARLICH zum selma-Repo ein neues,
# selbstaendiges Selma-Projekt an:
#   * eigener .selma-Workspace (eigene MEMORY, Skills, Template-Dateien)
#   * eigener Gateway/WebChat-Port
#   * Phoenix (UI :6006 / OTLP :4317) wird von ALLEN Projekten GEMEINSAM genutzt
#
# Nutzung:
#   ./create_new_selma_project.sh <name> [gateway_port]
#     <name>          z.B. "sozialmedia"  ->  $HOME/workspace/<name>
#                     (erlaubt: [a-z0-9_-]+)
#     [gateway_port]  optional, Default 8001  (8000/6006/4317/8501/11434 reserviert)
#
# Das Script erzeugt (bewusst OHNE Start — start.sh bleibt zur Prüfung):
#   <project>/venv         Symbolischer Link -> selma venv   (SELMA_VENV)
#   <project>/.selma/      selma.json (WebChat auf gateway_port, Modell vom
#                          Hauptprojekt uebernommen), workspace-Templates, skills, images/
#   <project>/start.sh     Gateway + Streamlit mit eigener Logik, Phoenix geteilt
#   ~/workspace/SELMA_REGISTRY.md  (SELMA_REG) — zentrales Register
#
# Phoenix-Lifecycle (gemeinsame Instanz):
#   * Start: Phoenix erreichbar -> fertig. Sonst starten (mit Kennzeichen
#            SELMA_PHOENIX_OWNER=1) + warten bis antwortet.
#   * Stop (Reihenfolge: erst eigenes Gateway): Laeuft NOCH ein selma.gateway?
#            -> Phoenix laess ich laufen (hoert auf die anderen auf).
#            -> Kein selma.gateway mehr: genau die MARKIERTEN Phoenix-Prozesse
#               (Kennzeichen SELMA_PHOENIX_OWNER=1) beenden.
#            Manuell gestartete Phoenix (kein Kennzeichen) werden NIEMALS
#            beendet. Kein Counter-File, kein Zustand ausserhalb der Prozesse.
#   * Niemand muss auf manuelle Kills zuruckversichert werden — wer von Hand
#     Prozesse totet, ist fur die Folgen verantwortlich (bewusster Tradeoff).
#
set -euo pipefail

# ---------------------------------------------------------------------------
# 1. Parameter
# ---------------------------------------------------------------------------
if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    echo "Ungültige Parameter."
    echo "Nutzung: $0 <name> [gateway_port]"
    exit 1
fi

PROJECT_NAME="$1"
GATEWAY_PORT="${2:-8001}"
SELMA_VENV="${SELMA_VENV:-$HOME/workspace/selma/venv}"
SELMA_REPO="${SELMA_REPO:-$HOME/workspace/selma}"
SELMA_REG="${SELMA_REG:-$HOME/workspace/SELMA_REGISTRY.md}"

PROJECT_PARENT_DIR="$HOME/workspace"
PROJECT_DIR="${PROJECT_PARENT_DIR}/${PROJECT_NAME}"

if ! [[ "$PROJECT_NAME" =~ ^[a-z0-9_-]+$ ]]; then
    echo "Fehler: Projektname '$PROJECT_NAME' muss nur Kleinbuchstaben, Ziffern, _ oder - enthalten."
    exit 1
fi
if ! [[ "$GATEWAY_PORT" =~ ^[0-9]+$ ]] || [ "$GATEWAY_PORT" -lt 1 ] || [ "$GATEWAY_PORT" -gt 65535 ]; then
    echo "Fehler: gateway_port muss eine gültige Zahl (1-65535) sein: '$GATEWAY_PORT'"
    exit 1
fi
for reserved in 8000 6006 4317 8501 11434; do
    if [ "$GATEWAY_PORT" -eq "$reserved" ]; then
        echo "Fehler: Port $GATEWAY_PORT ist reserviert (8000 = Hauptprojekt, 6006/4317 = Phoenix, 8501 = Streamlit-Standard, 11434 = Ollama)."
        exit 1
    fi
done

# ---------------------------------------------------------------------------
# 2. Abhängigkeiten
# ---------------------------------------------------------------------------
echo "=== Abhängigkeiten prüfen ==="
command -v curl >/dev/null 2>&1 || { echo "Fehler: curl fehlt."; exit 1; }
[ -x "$SELMA_VENV/bin/python" ] || {
    echo "Fehler: Selma venv nicht gefunden unter: $SELMA_VENV"
    echo "  -> SELMA_VENV auf den echten Pfad setzen."
    exit 1
}
"$SELMA_VENV/bin/python" -c "import selma" >/dev/null 2>&1 || {
    echo "Fehler: 'selma' ist im venv nicht importierbar (fehlendes Editable-Install?)."
    echo "  -> Im venv: python -m pip install --editable $SELMA_REPO"
    exit 1
}
[ -d "$SELMA_REPO/setup/templates" ] || { echo "Fehler: Templates fehlen: $SELMA_REPO/setup/templates"; exit 1; }
[ -d "$SELMA_REPO/setup/skills" ]    || { echo "Fehler: Skills fehlen:    $SELMA_REPO/setup/skills";    exit 1; }

echo "  Projekt:          $PROJECT_NAME"
echo "  Ziel:             $PROJECT_DIR"
echo "  venv (gespiegelt): $SELMA_VENV"
echo "  Selma-Repo:       $SELMA_REPO"
echo "  Gateway-Port:     :$GATEWAY_PORT"
echo "  Streamlit:        :8501 (via STREAMLIT_PORT ueberschreibbar)"
echo "  Phoenix:          gemeinsam auf :6006 (OTLP :4317)"
echo "  Registry:         $SELMA_REG"
echo ""

# ---------------------------------------------------------------------------
# 3. Ziel & Registry
# ---------------------------------------------------------------------------
if [ -e "$PROJECT_DIR" ]; then
    echo "FEHLER: Ziel existiert bereits: $PROJECT_DIR"
    echo "  -> Nicht ueberschrieben. Alten Stand loeschen oder neuen Namen waehlen."
    exit 1
fi
if [ -f "$SELMA_REG" ] && grep -qE "^### ${PROJECT_NAME}\s*\(" "$SELMA_REG"; then
    echo "FEHLER: Registry-Eintrag für '$PROJECT_NAME' existiert bereits: $SELMA_REG"
    echo "  -> Alten Registry-Eintrag loeschen und dann neu anlegen."
    exit 1
fi

# ---------------------------------------------------------------------------
# 4. Verzeichnis + venv-Link + src-Symlink (geteilter Code, kein Duplizieren)
# ---------------------------------------------------------------------------
[ -d "$SELMA_REPO/src" ] || { echo "Fehler: $SELMA_REPO/src fehlt (SELMA_REPO falsch?)"; exit 1; }
mkdir -p "$PROJECT_PARENT_DIR"
mkdir "$PROJECT_DIR"
ln -s "$SELMA_VENV" "$PROJECT_DIR/venv"
ln -s "$SELMA_REPO/src" "$PROJECT_DIR/src"
echo "  → Projektverzeichnis:    $PROJECT_DIR"
echo "  → venv (Symbolischer Link): $PROJECT_DIR/venv -> $SELMA_VENV"
echo "  → src  (Symbolischer Link): $PROJECT_DIR/src -> $SELMA_REPO/src"
echo ""

# ---------------------------------------------------------------------------
# 5. selma.setup + Templates/Skills/images aus dem Selma-Repo kopieren
#    (setup() findet Templates relativ zum Basisverzeichnis nicht mehr —
#     das ist erwartet und wird hier kompakt nachgeholt, ohne Ueberschreiben.)
# ---------------------------------------------------------------------------
echo "=== Selma-Setup ==="
( cd "$PROJECT_DIR" && "$PROJECT_DIR/venv/bin/python" -m selma.setup )

WS="${PROJECT_DIR}/.selma/workspace"
mkdir -p "$WS" "$PROJECT_DIR/images"
for f in "$SELMA_REPO"/setup/templates/*; do
    [ -f "$f" ] || continue
    base="$(basename "$f")"
    [ -e "$WS/$base" ] && continue
    cp "$f" "$WS/$base"
    echo "  → Template:  workspace/$base"
done
for s in "$SELMA_REPO"/setup/skills/*/; do
    sname="$(basename "$s")"
    [ -f "${s}SKILL.md" ] || continue
    mkdir -p "$WS/skills/$sname"
    ( cd "$s" && cp -rn . "$WS/skills/$sname"/ ) 2>/dev/null || true
    if [ ! -f "$WS/skills/$sname/SKILL.md" ]; then
        echo "  → Skill:     WARNUNG: skills/$sname/SKILL.md fehlt nach Kopie!"
    else
        echo "  → Skill:     workspace/skills/$sname"
    fi
done
if [ -d "$SELMA_REPO/setup/images" ]; then
    cp -rn "$SELMA_REPO/setup/images/." "$PROJECT_DIR"/images/ 2>/dev/null || true
    n_img="$(find "$PROJECT_DIR/images" -maxdepth 1 -type f | wc -l)"
    if [ "$n_img" -eq 0 ]; then
        echo "  → images/     WARNUNG: images-Verzeichnis bleibt leer (Quelle leer oder Kopie fehlgeschlagen)!"
    else
        echo "  → images/     ${n_img} Datei(en) (aus $SELMA_REPO/setup/images)"
    fi
else
    echo "  → images/     (keine Quelle unter $SELMA_REPO/setup/images — übersprungen)"
fi
echo ""

# ---------------------------------------------------------------------------
# 6. selma.json: WebChat aktiv + Port + Modell des Hauptprojekts uebernehmen
# ---------------------------------------------------------------------------
SELMA_JSON="${PROJECT_DIR}/.selma/selma.json"
[ -f "$SELMA_JSON" ] || { echo "Fehler: selma.json fehlt nach Setup: $SELMA_JSON"; exit 1; }
"$PROJECT_DIR/venv/bin/python" - "$SELMA_JSON" "$GATEWAY_PORT" "${SELMA_REPO}/.selma/selma.json" <<'PY'
import json, sys
from pathlib import Path
out = Path(sys.argv[1]); port = int(sys.argv[2]); ref = Path(sys.argv[3])
cfg = json.loads(out.read_text())
if ref.exists():
    r = json.loads(ref.read_text())
    for k in ("model", "session", "memory"):
        if isinstance(r.get(k), dict):
            cfg[k] = r[k]
w = cfg.setdefault("channels", {}).setdefault("webchat", {})
w.update(enabled=True, host="127.0.0.1", port=port, log_level="info")
out.write_text(json.dumps(cfg, indent=4) + "\n")
print("  →", out, "| webchat:", json.dumps(w), "| model:", cfg.get("model", {}).get("model"))
PY
echo ""

# ---------------------------------------------------------------------------
# 7. start.sh generieren
# ---------------------------------------------------------------------------
echo "=== start.sh generieren ==="
cat > "$PROJECT_DIR/start.sh" <<'START_SH_TMPL'
#!/usr/bin/env bash
# start.sh — von create_new_selma_project.sh generiert
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
START_SH_TMPL
chmod +x "$PROJECT_DIR/start.sh"
bash -n "$PROJECT_DIR/start.sh"
echo "  → start.sh angelegt (syntax-OK): $PROJECT_DIR/start.sh"
echo ""

# ---------------------------------------------------------------------------
# 8. Registry
# ---------------------------------------------------------------------------
echo "=== Registry aktualisieren ==="
REG_DIR="$(dirname "$SELMA_REG")"
mkdir -p "$REG_DIR"
if [ ! -f "$SELMA_REG" ]; then
    cat > "$SELMA_REG" <<'REG_HEADER'
# SELMA_REGISTRY.md — Register aller lokalen Selma-Projekt-Instanzen.

Phoenix ist GEMEINSAM (UI :6006, OTLP :4317) und wird ohne Counter-Datei
gesteuert: Läuft beim Stoppen eines Projekts noch irgendein selma.gateway,
bleibt Phoenix am Leben; ist keines mehr da, wird genau der mit dem
Kennzeichen SELMA_PHOENIX_OWNER=1 markierte Phoenix beendet.
Manuell gestartete Phoenix bleiben niemals angetastet.

REG_HEADER
fi
{
    echo ""
    echo "### ${PROJECT_NAME} (angelegt $(date -u +%Y-%m-%dT%H:%M:%SZ))"
    echo ""
    echo "| Feld         | Wert                                          |"
    echo "|------------|-----------------------------------------------|"
    echo "| Root         | ${PROJECT_DIR} |"
    echo "| venv         | ${SELMA_VENV} (Link: ${PROJECT_DIR}/venv) |"
    echo "| Gateway      | 127.0.0.1:${GATEWAY_PORT}                          |"
    echo "| Streamlit    | 127.0.0.1:8501 (STREAMLIT_PORT)                   |"
    echo "| Phoenix      | gemeinsam :6006 (OTLP :4317)                       |"
    echo "| Selma-Ws     | ${PROJECT_DIR}/.selma                        |"
    echo "| start.sh     | ${PROJECT_DIR}/start.sh                        |"
    echo "| Logs         | ${PROJECT_DIR}/gateway.log, streamlit.log, phoenix.log |"
} >> "$SELMA_REG"
echo "  → Registry aktualisiert: $SELMA_REG"
echo ""

# ---------------------------------------------------------------------------
# 9. Zusammenfassung
# ---------------------------------------------------------------------------
echo "=== Projekt angelegt (bewusst NOCH NICHT gestartet) ==="
echo ""
echo "  Nächste Schritte (dein Go):"
echo "  1) Prüfung:  $PROJECT_DIR/start.sh  und  ${PROJECT_DIR}/.selma/selma.json"
echo "  2) Testlauf (ggf. parallel zu laufenden Projekten):"
echo "       cd ${PROJECT_DIR} && ./start.sh        # Ctrl-C stoppt sauber"
echo "  3) Bei zwei parallelen Projekten: zweites mit STREAMLIT_PORT=8502"
echo ""
echo "  Registry: $SELMA_REG"
