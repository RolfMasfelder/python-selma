#!/usr/bin/env bash
# ============================================================
# run_tests.sh — führt die automatisierten Unit-Tests aus.
#
# Nutzt das virtuelle Environment unter ./venv (siehe TOOLS.md)
# und zeigt am Ende die Coverage pro Modul (Ziel: > 80% gesamt).
#
# Aufruf:
#   cd <projekt-root>
#   bash tests/scripts/run_tests.sh            # Unit-Tests + Coverage
#   bash tests/scripts/run_tests.sh -x         # stoppt beim ersten Fehler
# ============================================================
set -euo pipefail

# Zum Projekt-Root wechseln (Script liegt in tests/scripts/).
cd "$(dirname "$0")/../.."

# Venv aktivieren.
if [ -f venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
else
  echo "FEHLER: venv/ nicht gefunden. Zuerst mit 'python -m venv venv' anlegen." >&2
  exit 1
fi

EXTRA_ARGS=()
if [ "${1:-}" = "-x" ]; then
  EXTRA_ARGS+=("--exitfirst")
fi

python -m pytest tests/unit \
  --cov=selma \
  --cov-report=term-missing \
  "${EXTRA_ARGS[@]}"
