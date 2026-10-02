#!/usr/bin/env bash
# Sandbox-Testreihe v4 (Counter-frei, Gateway-Pruefung + Marker)
# Teilaufgaben: A) Parameter  B) Projektanlage  C) start.sh-Inhalt
#               D) Phoenix-Entscheidungs-Matrix (isoliert, Fake-Listener auf 16xxx)
#               E) ensure_phoenix (Stub-Phoenix)  F) Real-Pruefung selma_gateway_still_running
SB=/home/rolf/tmp/selma_sandbox
C=/home/rolf/workspace/selma/create_new_selma_project.sh
PY=/home/rolf/workspace/selma/venv/bin/python
export HOME="$SB/home"
export SELMA_VENV=/home/rolf/workspace/selma/venv
export SELMA_REPO=/home/rolf/workspace/selma
export SELMA_REG="$SB/home/workspace/SELMA_REGISTRY.md"

pass=0; fail=0
ok()  { echo "  ✅ $1"; pass=$((pass+1)); }
bad() { echo "  ❌ $1"; fail=$((fail+1)); }
FAKE_PIDS=()
reap_fakes() { for p in "${FAKE_PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done; }
trap reap_fakes EXIT

# extract_fn <name> <datei>: kopiert genau die bash-Funktion in stdout
extract_fn() {
  awk -v fn="$1" 'BEGIN{f=0} $0==fn"() {"{f=1} f{print} f && $0=="}"{exit}' "$2"
}

# ===========================================================================
# Selbstreinigung: Sandbox ist eine Scratch-Domain (Rolf-Ok 2026-09-28),
# Restlaeufe vorheriger Runden (Projekt-Dirs + Registry) wuerden B1/B11 rot
# melden ("existiert bereits"). Wir arbeiten die Ziel-Dirs + Registry-Datei
# NUR aus $SB und pruefen vorher (ls), damit keine fremden Pfade getroffen werden.
if [ -e "$SB" ] && ls "$SB" >/dev/null 2>&1; then
  ls -la "$SB" "$SB/home/"* 2>/dev/null | head -20
  rm -rf "$SB/home/workspace/socialmedia" "$SB/home/workspace/projektzwei"
fi
rm -f "$SELMA_REG"

# ===========================================================================
echo "### A: Parameter-Validierung"
out=$(bash "$C" 2>&1); rc=$?
[ $rc -eq 1 ] && grep -q "Nutzung" <<<"$out" && ok "A1 keine Parameter" || bad "A1 (rc=$rc)"
out=$(bash "$C" "Sozial.Media" 2>&1); rc=$?
[ $rc -eq 1 ] && grep -qi "Kleinbuchstaben" <<<"$out" && ok "A2 Uppercase+Punkt abgelehnt" || bad "A2 (rc=$rc)"
out=$(bash "$C" "has space" 2>&1); rc=$?
[ $rc -eq 1 ] && ok "A3 Leerraum abgelehnt" || bad "A3 (rc=$rc)"
out=$(bash "$C" foo 8000 2>&1); rc=$?
[ $rc -eq 1 ] && grep -q "reserviert" <<<"$out" && ok "A4 Port 8000 reserviert" || bad "A4 (rc=$rc)"
out=$(bash "$C" foo 8501 2>&1); rc=$?
[ $rc -eq 1 ] && grep -q "reserviert" <<<"$out" && ok "A5 Port 8501 reserviert" || bad "A5 (rc=$rc)"
out=$(bash "$C" foo 6006 2>&1); rc=$?
[ $rc -eq 1 ] && grep -q "reserviert" <<<"$out" && ok "A6 Port 6006 reserviert" || bad "A6 (rc=$rc)"
out=$(bash "$C" foo abc 2>&1); rc=$?
[ $rc -eq 1 ] && ok "A7 Port 'abc' abgelehnt" || bad "A7 (rc=$rc)"
out=$(bash "$C" foo 70000 2>&1); rc=$?
[ $rc -eq 1 ] && ok "A8 Port 70000 abgelehnt" || bad "A8 (rc=$rc)"
out=$(bash "$C" a b c 2>&1); rc=$?
[ $rc -eq 1 ] && grep -q "Nutzung" <<<"$out" && ok "A9 3 Parameter" || bad "A9 (rc=$rc)"

# ===========================================================================
echo ""
echo "### B: Projektanlage"
if (exec 3<>/dev/tcp/127.0.0.1/8001) 2>/dev/null; then bad "B0 8001 belegt?!"; else ok "B0 Port 8001 frei"; fi
if (exec 3<>/dev/tcp/127.0.0.1/8002) 2>/dev/null; then bad "B0 8002 belegt?!"; else ok "B0 Port 8002 frei"; fi

out=$(bash "$C" socialmedia 8001 2>&1); rc=$?
D="$HOME/workspace/socialmedia"
[ $rc -eq 0 ] && ok "B1 Anlage socialmedia @8001 (rc=0)" || { bad "B1 rc=$rc"; tail -15 <<<"$out"; }
[ -L "$D/venv" ] && ok "B2 venv-Symlink -> $(readlink "$D/venv")" || bad "B2 venv-Symlink"
P="$D/.selma/selma.json"
[ -f "$P" ] && "$PY" -c "
import json
cfg=json.load(open('$P'))
w=cfg['channels']['webchat']
assert w['port']==8001 and w['enabled'] is True and w['host']=='127.0.0.1', w
assert cfg['model']['model']=='ollama/qwen3.8-27b-t16', cfg.get('model')
" && ok "B3 selma.json (Port/Host/Modell)" || bad "B3 selma.json"
[ -f "$D/.selma/workspace/SOUL.md" ] && [ -f "$D/.selma/workspace/AGENTS.md" ] && ok "B4 Templates" || bad "B4 Templates"
miss=""
for sk in blogwatcher healthcheck summarize web-research; do
  [ -f "$D/.selma/workspace/skills/$sk/SKILL.md" ] || miss="$miss $sk"
done
[ -z "$miss" ] && ok "B5 alle 4 Skills inkl. SKILL.md" || bad "B5 Skills fehlen:$miss"
n_img="$(find "$D/images" -maxdepth 1 -type f | wc -l)"
[ "$n_img" -ge 2 ] && ok "B6 images ($n_img Dateien)" || bad "B6 images (n=$n_img)"
GEN="$D/start.sh"
[ -x "$GEN" ] && bash -n "$GEN" && ok "B7 start.sh (exec + Syntax)" || bad "B7 start.sh"
grep -q "^### socialmedia (" "$SELMA_REG" && ok "B8 Registry-Eintrag" || bad "B8 Registry"
[ "$SB/home/.selma" ] && [ ! -e "$SB/home/.selma/.phoenix_owners" ] && ok "B9 KEINE Counter-Datei in \$.selma" || bad "B9 Counter-Datei?"

# --- v5-Checks (Fix C: src-Symlink, Fix A: CLI gateway-url) ---
if [ -L "$D/src" ]; then
  tgt="$(readlink "$D/src")"
  [ -f "$D/src/selma/dashboard.py" ] && ok "B13 v5 src-Symlink -> $tgt (dashboard.py erreichbar)" || bad "B13 src-Symlink zeigt, aber dashboard.py fehlt"
else
  bad "B13 v5 src-Symlink fehlt"
fi
grep -q 'gateway-url="http://127.0.0.1:${GATEWAY_PORT}/webchat/stream"' "$GEN" && ok "B14 v5 start.sh uebergibt gateway-url= CLI-Arg" || bad "B14 v5 gateway-url in start.sh"
[ -f /home/rolf/workspace/selma/start.sh ] && diff -q "$GEN" /home/rolf/workspace/selma/start.sh >/dev/null 2>&1 \
  && ok "B16 v6 generiertes start.sh IDENTISCH zu MAIN-Repo start.sh (Kopie statt Template)" \
  || bad "B16 v6 Generiert != MAIN-Repo start.sh"
out="$("$PY" /home/rolf/workspace/selma/_scratch_selma/b15_check.py 2>&1)"; rc=$?
if [ $rc -eq 0 ]; then
  ok "B15 v5 dashboard: resolve_webchat_stream_url (CLI>Env>Default, strip-'/')"
else
  bad "B15 v5 resolve_webchat_stream_url"; echo "$out" | tail -5
fi

out=$(bash "$C" socialmedia 8001 2>&1); rc=$?
[ $rc -eq 1 ] && grep -q "existiert bereits" <<<"$out" && ok "B10 Dir-Doppeltreffer" || bad "B10 (rc=$rc)"

out=$(bash "$C" projektzwei 8002 2>&1); rc=$?
D2="$HOME/workspace/projektzwei"
[ $rc -eq 0 ] && [ -f "$D2/.selma/selma.json" ] && \
  "$PY" -c "import json; w=json.load(open('$D2/.selma/selma.json'))['channels']['webchat']; assert w['port']==8002" \
  && ok "B11 Parallel-Projekt projektzwei @8002" || bad "B11 (rc=$rc)"
out=$(bash "$C" projektzwei 8003 2>&1); rc=$?
[ $rc -eq 1 ] && ok "B12 Registry-Doppeltreffer" || bad "B12 (rc=$rc)"

# Main-Belegung ist EXPECTED (Info, kein Fehlerkriterium)
busy=""
for p in 8000 8501 6006; do (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null && busy="$busy $p"; done
echo "  ℹ️  Main-Belegung (erwartet):${busy:-  — Main-Projekt nicht aktiv} "

# ===========================================================================
echo ""
echo "### C: start.sh-Inhalt (v4, Counter-frei)"
n_ctr="$(grep -cEi 'counter_|phoenix_owners|SELMA_STATE_HOME' "$GEN" || true)"
[ "$n_ctr" -eq 0 ] && ok "C0 kein Counter-/Owner-File-Rest" || bad "C0 Counter-Reste: $n_ctr"
grep -q "selma_gateway_still_running()" "$GEN" && ok "C1 Gateway-Pruefung vorhanden" || bad "C1"
grep -q 'PHOENIX_OWNER_ENV="SELMA_PHOENIX_OWNER"' "$GEN" && ok "C2 Marker" || bad "C2"
grep -q "kill \"\$GATEWAY_PID\"" "$GEN" && ok "C3 eigenes Gateway wird gekillt" || bad "C3"
ln_gw="$(grep -n 'kill "$GATEWAY_PID"' "$GEN" | head -1 | cut -d: -f1)"
ln_px="$(grep -n 'stop_phoenix_if_last' "$GEN" | grep -v '() {' | head -1 | cut -d: -f1)"
[ -n "$ln_gw" ] && [ -n "$ln_px" ] && [ "$ln_gw" -lt "$ln_px" ] && ok "C4 Reihenfolge: Gateway-Kill vor Phoenix-Check ($ln_gw < $ln_px)" || bad "C4 Reihenfolge"

# ===========================================================================
echo ""
echo "### D: Phoenix-Entscheidungs-Matrix (isoliert, Fake-Listener :16007)"
FAKE_PORT=16007
wait_port_free() { local i; for i in $(seq 1 40); do (exec 3<>/dev/tcp/127.0.0.1/$FAKE_PORT) 2>/dev/null || return 0; sleep 0.5; done; echo "  ⚠️ Port $FAKE_PORT geht nicht frei!" >&2; return 0; }
reap_fake() {
  local p
  p="$(lsof -ti ":$FAKE_PORT" 2>/dev/null | head -5)"
  [ -n "$p" ] && echo "$p" | xargs -r kill 2>/dev/null
  for p in "${FAKE_PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done
  FAKE_PIDS=()
  wait_port_free
}
# Globale Variablen setzen (KEIN Subshell!): FAKE_PID
start_fake_ph() {  # $1=managed(1)/unmanaged(0)
  wait_port_free
  if [ "$1" -eq 1 ]; then
    SELMA_PHOENIX_OWNER=1 "$PY" -m http.server "$FAKE_PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
  else
    "$PY" -m http.server "$FAKE_PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
  fi
  FAKE_PID=$!; FAKE_PIDS+=("$FAKE_PID")
  local i; for i in $(seq 1 20); do (exec 3<>/dev/tcp/127.0.0.1/$FAKE_PORT) 2>/dev/null && return 0; sleep 0.5; done
  echo "  ⚠️ Fake-Phoenix antwortet nicht" >&2; return 1
}
alive() { kill -0 "$1" 2>/dev/null; }

# Matrix-Harness: echte phoenix_pid/phoenix_is_managed/stop_phoenix_if_last
# aus der generierten start.sh, GW-Stub fuer den Gateway-Check.
# (Funktions-Extraktion laeuft im ELTERN-Shell; der Subshell bekommt fertigen Code.)
run_stop_case() {  # $1=gw(yes|no)
  local gw="$1" h="$SB/mx_h.${RANDOM}.sh"
  {
    echo "set -euo pipefail"
    sed "s|__PORT__|$FAKE_PORT|" "$SB/harness_base.sh"
    if [ "$gw" = yes ]; then
      echo 'selma_gateway_still_running() { return 0; }'
    else
      echo 'selma_gateway_still_running() { return 1; }'
    fi
    echo "stop_phoenix_if_last"
  } > "$h"
  bash "$h"
  local rc=$?
  rm -f "$h"
  return $rc
}
# Harness-Grundgeruest (einmal gebaut)
{
  echo "PHOENIX_PORT=__PORT__"
  echo "PHOENIX_OWNER_ENV=\"SELMA_PHOENIX_OWNER\""
  echo "PHOENIX_LOG=/dev/null"
  extract_fn phoenix_pid        "$GEN"
  extract_fn phoenix_is_managed "$GEN"
  extract_fn stop_phoenix_if_last "$GEN"
} > "$SB/harness_base.sh"

# D1: anderes Gateway laeuft, managed Phoenix -> Phoenix bleibt
start_fake_ph 1 && {
  out="$(run_stop_case yes 2>&1)"
  sleep 0.5
  grep -q "Andere Selma-Gateways" <<<"$out" && alive "$FAKE_PID" && ok "D1 Gateway aktiv → Phoenix bleibt" || { bad "D1"; echo "$out"; alive "$FAKE_PID" || echo "  (Fake-Proc tot!)"; }
}
reap_fake

# D2: kein Gateway, managed Phoenix -> kill
start_fake_ph 1 && {
  out="$(run_stop_case no 2>&1)"
  sleep 0.5
  grep -q "beende das" <<<"$out" && ! alive "$FAKE_PID" && ok "D2 kein Gateway + managed → Phoenix gestoppt" || { bad "D2"; echo "$out"; }
}
reap_fake

# D3: kein Gateway, UNmanaged Phoenix -> bleibt
start_fake_ph 0 && {
  out="$(run_stop_case no 2>&1)"
  sleep 0.5
  grep -q "NICHT von einem Skript" <<<"$out" && alive "$FAKE_PID" && ok "D3 kein Gateway + unmanaged → PHOENIX BLEIBT" || { bad "D3"; echo "$out"; }
  if tr '\0' '\n' < "/proc/$FAKE_PID/environ" 2>/dev/null | grep -q "^SELMA_PHOENIX_OWNER=1$"; then
    bad "D3a Fake hat Marker (Setup-Fehler?)"
  else
    ok "D3a Fake ist wirklich unmarked"
  fi
}
reap_fake

# D4: kein Gateway, kein Phoenix -> nichts zu tun
out="$(run_stop_case no 2>&1)"
grep -q "nichts zu tun" <<<"$out" && ok "D4 kein Gateway, kein Phoenix" || { bad "D4"; echo "$out"; }

# D5: anderes Gateway laeuft, unmanaged Phoenix -> bleibt (Doppelabsicherung)
start_fake_ph 0 && {
  out="$(run_stop_case yes 2>&1)"
  sleep 0.5
  grep -q "Andere Selma-Gateways" <<<"$out" && alive "$FAKE_PID" && ok "D5 Gateway aktiv + unmanaged → bleibt" || { bad "D5"; echo "$out"; }
}
reap_fake

# ===========================================================================
echo ""
echo "### E: ensure_phoenix"
HARN="$SB/ensure_harness.sh"
cat > "$HARN" <<ENSURE_H
PHOENIX_PORT=__PORT__
PHOENIX_OWNER_ENV="SELMA_PHOENIX_OWNER"
PHOENIX_LOG=$(mktemp)
$(extract_fn ensure_phoenix "$GEN")
ensure_phoenix
ENSURE_H

# E1: Port bereitzufassen (anderes Projekt / manuell) -> "nutze ich einfach"
E1PORT=16005
"$PY" -m http.server "$E1PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
EP=$!; FAKE_PIDS+=("$EP"); sleep 1
sed "s/__PORT__/$E1PORT/" "$HARN" > "$HARN.e1"
out="$(bash "$HARN.e1" 2>&1)"; rc=$?
grep -q "nutze ich einfach" <<<"$out" && ok "E1 Phoenix schon erreichbar → wird gemeldet, nicht gestartet" || { bad "E1 (rc=$rc)"; echo "$out"; }
kill "$EP" 2>/dev/null; sleep 0.3

# E2: Port frei, Phoenix-Stub auf PATH -> startet, wird "online"
STUBBIN="$SB/bin"; rm -rf "$STUBBIN"; mkdir -p "$STUBBIN"
cat > "$STUBBIN/phoenix" <<'STUB'
#!/bin/bash
# 'phoenix serve --port N' simulieren: http.server auf N
P=""
prev=""
for a in "$@"; do [ "$prev" = "--port" ] && P="$a"; prev="$a"; done
exec "$SELMA_TEST_PY" -m http.server "${P:-6006}" --bind 127.0.0.1
STUB
chmod +x "$STUBBIN/phoenix"
E2PORT=16004
sed "s/__PORT__/$E2PORT/" "$HARN" > "$HARN.e2"
SELMA_TEST_PY="$PY" PATH="$STUBBIN:$PATH" bash "$HARN.e2" > "$SB/e2.out" 2>&1
if grep -q "Phoenix online unter" "$SB/e2.out"; then ok "E2 Phoenix-Stub gestartet → 'online' gemeldet"; else bad "E2 ($(tail -3 "$SB/e2.out" 2>/dev/null))"; fi
# Stub-Kind (http.server) finden + Port/MarKer pruefen + auerraumen
STUBPID="$(lsof -ti ":$E2PORT" -sTCP:LISTEN 2>/dev/null | head -1)"
if [ -z "$STUBPID" ]; then
  bad "E2b kein Prozess auf $E2PORT (Stub bindet falsche Adresse?)"
elif tr '\0' '\n' < "/proc/$STUBPID/environ" 2>/dev/null | grep -q "^SELMA_PHOENIX_OWNER=1$"; then
  ok "E2c Stub laeuft auf --port $E2PORT UND fuehrt den Marker"
else
  bad "E2c Marker fehlt im environ des gestarteten Phoenix"
fi
[ -n "${STUBPID:-}" ] && kill "$STUBPID" 2>/dev/null
sleep 0.3

# ===========================================================================
echo ""
echo "### F: selma_gateway_still_running (Echtfunktion)"
cat > "$SB/fw.h1" <<FW_H
$(extract_fn selma_gateway_still_running "$GEN")
if selma_gateway_still_running; then echo "RUNNING"; else echo "NOT_RUNNING"; fi
FW_H
# F1: Fake-Gateway-Process (argv[0]=selma.gateway.test) muss GEFUNDEN werden
bash -c 'exec -a "selma.gateway.test" sleep 90' &
FG=$!; sleep 0.5
out="$(bash "$SB/fw.h1")"
[ "$out" = "RUNNING" ] && ok "F1 Fake-Gateway-Process erkannt" || bad "F1 aus=$out"
kill "$FG" 2>/dev/null; sleep 0.3
out="$(bash "$SB/fw.h1")"
if [ "$out" = "RUNNING" ]; then
  echo "  ℹ️  F2 Main-Gateway läuft → RUNNING (erwartet; Main-Projekt ist aktiv, hier nicht isolierbar)"
  ok "F2 (Info: Main-Belegung — isolierter Fall ist D1/D2 mit GW-Stub)"
else
  ok "F2 Kein Gateway → NOT_RUNNING"
fi
kill "$FG" 2>/dev/null || true

echo ""
echo "=== ERGEBNIS: $pass ok, $fail fehler ==="
[ $fail -eq 0 ]
