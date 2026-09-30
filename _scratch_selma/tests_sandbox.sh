#!/usr/bin/env bash
# Sandbox-Testreihe fuer create_new_selma_project.sh (v3)
SB=/home/rolf/tmp/selma_sandbox
C=/home/rolf/workspace/selma/create_new_selma_project.sh
export HOME="$SB/home"
export SELMA_VENV=/home/rolf/workspace/selma/venv
export SELMA_REPO=/home/rolf/workspace/selma
export SELMA_REG="$SB/home/workspace/SELMA_REGISTRY.md"
export SELMA_STATE_HOME="$SB/home/.selma"

pass=0; fail=0
ok()  { echo "  ✅ $1"; pass=$((pass+1)); }
bad() { echo "  ❌ $1"; fail=$((fail+1)); }

echo "### T1: keine Parameter -> Usage-Fehler, exit 1"
out=$(bash "$C" 2>&1); rc=$?
[ $rc -eq 1 ] && grep -q "Nutzung" <<<"$out" && ok "T1 (exit 1 + Usage)" || bad "T1 (rc=$rc): $out"

echo "### T2: Name 'Sozial.Media' -> abgelehnt, exit 1"
out=$(bash "$C" "Sozial.Media" 2>&1); rc=$?
[ $rc -eq 1 ] && grep -qi "Kleinbuchstaben" <<<"$out" && ok "T2" || bad "T2 (rc=$rc)"

echo "### T3: Name 'has space' -> abgelehnt"
out=$(bash "$C" "has space" 2>&1); rc=$?
[ $rc -eq 1 ] && ok "T3" || bad "T3 (rc=$rc)"

echo "### T4: Port 8000 -> reserviert, exit 1"
out=$(bash "$C" foo 8000 2>&1); rc=$?
[ $rc -eq 1 ] && grep -q "reserviert" <<<"$out" && ok "T4" || bad "T4 (rc=$rc)"

echo "### T5: Port 8501 -> reserviert, exit 1"
out=$(bash "$C" foo 8501 2>&1); rc=$?
[ $rc -eq 1 ] && grep -q "reserviert" <<<"$out" && ok "T5" || bad "T5 (rc=$rc)"

echo "### T6: Port 'abc' -> abgelehnt"
out=$(bash "$C" foo abc 2>&1); rc=$?
[ $rc -eq 1 ] && ok "T6" || bad "T6 (rc=$rc)"

echo "### T7: Port 70000 -> abgelehnt"
out=$(bash "$C" foo 70000 2>&1); rc=$?
[ $rc -eq 1 ] && ok "T7" || bad "T7 (rc=$rc)"

echo "### T8: 3 Parameter -> Usage-Fehler"
out=$(bash "$C" a b c 2>&1); rc=$?
[ $rc -eq 1 ] && grep -q "Nutzung" <<<"$out" && ok "T8" || bad "T8 (rc=$rc)"

echo "### T9: Projekt anlegen @8001 -> success"
out=$(bash "$C" socialmedia 8001 2>&1); rc=$?
D="$HOME/workspace/socialmedia"
[ $rc -eq 0 ] && ok "T9 rc=0" || bad "T9 rc=$rc: $(tail -5 <<<"$out")"
[ -L "$D/venv" ] && ok "T9 venv-Symlink -> $(readlink "$D/venv")" || bad "T9 venv-Symlink fehlt"
[ -f "$D/.selma/selma.json" ] && ok "T9 selma.json" || bad "T9 selma.json fehlt"
"$SELMA_VENV/bin/python" -c "
import json,sys
cfg=json.load(open('$D/.selma/selma.json'))
w=cfg['channels']['webchat']
assert w['port']==8001 and w['enabled'] and w['host']=='127.0.0.1', w
assert cfg['model']['model']=='ollama/qwen3.8-27b-t16', cfg.get('model')
" && ok "T9 selma.json Inhalt (Port 8001, Modell uebernommen)" || bad "T9 selma.json Inhalt"
[ -f "$D/.selma/workspace/SOUL.md" ] && ok "T9 Templates kopiert" || bad "T9 Templates fehlen"
miss=""
for sk in blogwatcher healthcheck summarize web-research; do
    [ -f "$D/.selma/workspace/skills/$sk/SKILL.md" ] || miss="$miss $sk"
done
[ -z "$miss" ] && ok "T9 alle 4 Skills inkl. SKILL.md" || bad "T9 Skills fehlen:$miss"
[ -f "$D/images/selma.png" ] && [ -f "$D/images/selma_portrait.png" ] && ok "T9 images kopiert (2 PNGs)" || bad "T9 images fehlen"
[ -x "$D/start.sh" ] && bash -n "$D/start.sh" && ok "T9 start.sh (exec + Syntax)" || bad "T9 start.sh"
grep -q "^### socialmedia (" "$SELMA_REG" && ok "T9 Registry-Eintrag" || bad "T9 Registry fehlt"

echo "### T10: Ziel existiert -> Refusal, exit 1"
out=$(bash "$C" socialmedia 8001 2>&1); rc=$?
[ $rc -eq 1 ] && grep -q "existiert bereits" <<<"$out" && ok "T10" || bad "T10 (rc=$rc)"

echo "### T11: Parallel-Projekt 'projektzwei' @8002 -> success"
out=$(bash "$C" projektzwei 8002 2>&1); rc=$?
D2="$HOME/workspace/projektzwei"
[ $rc -eq 0 ] && [ -f "$D2/.selma/selma.json" ] && \
"$SELMA_VENV/bin/python" -c "
import json; w=json.load(open('$D2/.selma/selma.json'))['channels']['webchat']; assert w['port']==8002
" && ok "T11 (Projekt 2, Port 8002)" || bad "T11 (rc=$rc)"

echo "### T12: Registry/Dir-Doppeltreffer -> Refusal"
out=$(bash "$C" projektzwei 8003 2>&1); rc=$?
[ $rc -eq 1 ] && ok "T12" || bad "T12 (rc=$rc)"

echo "### T13: Ports frei (8000/8001/8002/8501/6006)"
busy=""
for p in 8000 8001 8002 8501 6006; do
    if (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null; then busy="$busy $p"; fi
done
[ -z "$busy" ] && ok "T13 keine Ports belegt" || bad "T13 belegt:$busy"

echo ""
echo "=== ERGEBNIS: $pass ok, $fail fehler ==="
[ $fail -eq 0 ]
