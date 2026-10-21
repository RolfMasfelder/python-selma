"""`selma-dashboard` — Streamlit-Start für die INSTALLIERTE selma-Paketversion.

Warum dieses Modul existiert
----------------------------
`streamlit run` braucht einen *Dateipfad* zur App. War selma im Projekt nur
per `src/`-Symlink verfügbar, hieß das Startkommando in `start.sh`:

    streamlit run src/selma/dashboard.py --server.port=…

— und jedes Projekt brauchte deshalb einen Symlink auf die MAIN-Quellen.
Nicht mehr: selma ist jetzt ein normales installierbares Paket und
`importlib.util.find_spec("selma.dashboard")` liefert den Ort der
installierten `dashboard.py` (im venv; editiert → `src/`), OHNE das Modul
auszuführen. find_spec lädt nur den Pfad (keine Streamlit-Seiteneffekte) —
bewusst NICHT `import selma.dashboard`, das würde Top-Level-Code außerhalb
der Streamlit-Runtime ausführen.

Das Kommando startet Streamlit als Child-Prozess und übernimmt dessen
Exit-Code (Start-/Stop-Logik bleibt in `start.sh` zentral; ein Ctrl-C dort
killt beide PIDs via cleanup-trap).

Verträge:
* Läuft immer mit Python/dem venv, in dem selma installiert ist
  (Shebang des Console-Scripts).
* Relative Pfade in der App (z. B. `images/selma.png`) werden von Streamlit
  relativ zum ARBEITSDIR aufgelöst → aus dem Projektroot starten
  (start.sh ruft `cd "$(dirname $0)"` vor dem Start aus).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


def find_dashboard_path() -> Path | None:
    """Pfad der installierten `selma/dashboard.py` (None, falls selma fehlt)."""
    spec = importlib.util.find_spec("selma.dashboard")
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).resolve()


def build_command(app_path: Path, passthrough_args: list[str]) -> list[str]:
    """Streamlit-Aufruf bauen: `python -m streamlit run <app> [args…]`.

    `python -m streamlit` statt PATH-`streamlit`: deterministisch, unabhängig
    von PATH-Zustand.
    """
    return [sys.executable, "-m", "streamlit", "run", str(app_path), *passthrough_args]


def main() -> int:
    app_path = find_dashboard_path()
    if app_path is None:
        print(
            "Fehler: selma.dashboard im venv nicht gefunden.\n"
            "  → selma installieren: python3 -m venv venv && venv/bin/pip install ~/workspace/selma",
            file=sys.stderr,
        )
        return 1

    cmd = build_command(app_path, sys.argv[1:])
    print(f"Starte Selma-Dashboard → {app_path}")
    if sys.argv[1:]:
        print(f"  Streamlit-Args: {' '.join(sys.argv[1:])}")
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    raise SystemExit(main())
