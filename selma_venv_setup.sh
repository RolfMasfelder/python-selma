#!/usr/bin/env bash
# selma_venv_setup.sh — baut den Projekt-venv (ECHTE Umgebung, kein Symlink)
# und installiert selma aus dem MAIN-Repo (nicht-editable → Wheel-Kopie).
#
# Warum ein ECHTER venv statt Symlink auf den MAIN-venv:
#   * MAIN weiterentwickelt → Projektkopie in site-packages bleibt GÜLTIG,
#     bis bewusst upgegraded. (Symlink-venv: MAIN-Änderungen "rutschen"
#     stur in alle Projekte, ohne Versions-Kontrolle.)
#   * `pip install <repo>` erzeugt ein Wheel (pip build) UND installiert es
#     lokal. Versions-Pinning, saubere Abhängigkeitsauflösung, PyPI-kompatibel.
#   * selmalocalspezifische Extras (z. B. [audio]) können pro Projekt
#     nachinstalliert werden, ohne MAIN zu verändern:
#         pip install "~/workspace/selma[audio]"
#
# Aufruf (wird von create_new_selma_project.sh automatisch aufgerufen,
# kann aber auch manuell genutzt werden, z. B. bei Python-Upgrade):
#   ./selma_venv_setup.sh [ZIELVERZEICHNIS] [SELMA_REPO_Pfad]
#     [ZIELVERZEICHNIS]   Pfad, in dem ./venv entstehen soll (Default: CWD)
#     [SELMA_REPO_Pfad]   optional, Default $SELMA_REPO oder $HOME/workspace/selma
#
# Selbstschutz: bricht ab, wenn TARGET_DIR das MAIN-Repo wäre — so ein
# Install-Vorgang würde MAINs editable-Install durch ein Wheel überschreiben.
set -euo pipefail

TARGET_DIR="${1:-.}"
SELMA_REPO="${2:-${SELMA_REPO:-$HOME/workspace/selma}}"
[ -d "$SELMA_REPO/src" ] || { echo "Fehler: $SELMA_REPO/src fehlt — SELMA_REPO korrekt?"; exit 1; }
[ -f "$SELMA_REPO/pyproject.toml" ] || { echo "Fehler: $SELMA_REPO/pyproject.toml fehlt"; exit 1; }

# 1) Python-Interpreter wählen (priorisiert: SELMA_PYTHON → python3.13 → python3)
PY=""
for cand in "${SELMA_PYTHON:-}" python3.13 python3; do
    [ -n "$cand" ] && command -v "$cand" >/dev/null 2>&1 && { PY="$cand"; break; }
done
[ -n "$PY" ] || { echo "Fehler: kein passender Python-Interpreter gefunden (python3.13 / python3 / SELMA_PYTHON)."; exit 1; }
echo "  → Python: $("$PY" --version) ($("$PY" -c 'import sys; print(sys.executable)'))"

# 2) Arbeit in TARGET_DIR verorten (venv entsteht dort, nicht im CWD!)
cd "$TARGET_DIR"

# 3) Selbstschutz: TARGET_DIR darf NIE im MAIN-Repo liegen
#    (Pfad-Vergleich auf exakte Pfadkomponenten — "selma" darf nicht in
#     "selma_script_test" hineinmatchen!)
main_repo_abs="$(cd "$SELMA_REPO" && pwd -P)"
target_abs="$(pwd -P)"
if [ "$target_abs" = "$main_repo_abs" ] || [[ "$target_abs" == "$main_repo_abs"/* ]]; then
    echo "FEHLER: TARGET_DIR=$TARGET_DIR liegt im MAIN-Repo ($main_repo_abs) —"
    echo "  abgebrochen, MAIN-venv bleibt unangetastet."
    exit 1
fi

# 4) venv erstellen (falls schon vorhanden: beibehalten, selma dort neu installieren)
if [ -d venv ]; then
    echo "  → venv vorhanden, beibehalte."
else
    echo "  → Erstelle venv (python -m venv venv)…"
    "$PY" -m venv venv
fi
# Sanity: venv-Modul vorhanden
venv/bin/python -c "import venv" >/dev/null 2>&1 || { echo "Fehler: venv-Modul nicht verfügbar (broken venv?)."; exit 1; }

# 5) pip aktualisieren (leiser)
venv/bin/python -m pip install --quiet --upgrade pip

# 6) selma installieren (NICHT editierbar → echte Wheel-Kopie in site-packages)
main_venv_dir="${SELMA_REPO}/venv"
if [ -d "$main_venv_dir" ] && [ "$(pwd -P)/venv" = "$(cd "$main_venv_dir" 2>/dev/null && pwd -P)" ]; then
    echo "FEHLER: TARGET_DIR/venv WÄRE das MAIN-venv — abgebrochen."
    exit 1
fi
echo "  → Installiere selma aus $SELMA_REPO (nicht-editable)…"
venv/bin/pip install --quiet "$SELMA_REPO"

# 7) Verifikation: Version + Entry-Points vorhanden
V=$(venv/bin/pip show selma 2>/dev/null | awk '/^Version:/{print $2}')
[ -n "$V" ] || { echo "Fehler: 'selma' nach Installation nicht auffindbar."; exit 1; }
for ep in selma-gateway selma-setup selma-dashboard; do
    [ -x "venv/bin/$ep" ] || { echo "Fehler: Entry-Point $ep fehlt in venv/bin."; exit 1; }
done
echo "  → Selma-Version: $V"
echo "  → Entry-Points: 3/3 (selma-gateway, selma-setup, selma-dashboard)"
echo "FERTIG: Projekt-venv ist installiert (selma aus $SELMA_REPO)."
