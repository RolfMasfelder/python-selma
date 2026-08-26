# tests/ — Tests und Manuelle Skripte

```
tests/
├── unit/          # Automatisierte Unit-Tests (pytest, deterministisch, ohne Modell)
├── integration/   # (Reserviert) — Echte Integrationstests mit gemocktem LLM-Client
└── scripts/       # Manuelle Live-Skripte — brauchen ein laufendes Ollama & Co.
```

## Unit-Tests (`tests/unit/`)

Deterministische Tests mit Mocks — sie rufen **kein** echtes LLM-Modell auf.

- `test_unit_heartbeat.py` — Heartbeat-Logik
- `test_unit_memory.py` — Memory-Index / Memory-Dateien

### Ausführen

```bash
cd <projekt-root>
bash tests/scripts/run_tests.sh          # alle Unit-Tests + Coverage-Bericht
bash tests/scripts/run_tests.sh -x       # stoppt beim ersten Fehler
```

Alternativ direkt:

```bash
source venv/bin/activate
python -m pytest tests/unit --cov=selma --cov-report=term-missing
```

> **Ziel:** > 80% Coverage über das gesamte `selma`-Paket (siehe `MEMORY.md` / laufende Aufgabe).
> Aktuell schwache Punkte: `runtime.py`, `agent_session.py`, `session_store.py`, `my_tools.py`.

## Integrationstests (`tests/integration/`)

**Noch leer.** Geplant: echte Codepfade (`runtime.py`, `agent_session.py`, `webchat`-Channel)
zu testen, aber mit einem **gemockten Ollama/OpenAI-Client** (skriptet Events & Tool-Calls),
damit die Tests deterministisch und schnell bleiben. Abgrenzung zu `unit/`:
Integrationstests laufen über mehr als ein Modul (z.B. Session → Agent-Loop → Delivery).

## Manuelle Live-Skripte (`tests/scripts/`)

Diese Skripte sind **keine pytest-Tests** (pytest sammelt dort nichts).
Sie rufen ein **echtes, laufendes Ollama-Modell** auf und sind für manuelle
Funktionstests / Debugging gedacht. Vor der Ausführung:

1. Ollama läuft lokal (Default: `http://localhost:11434`),
2. Das konfigurierte Modell ist geladen, z.B. `ollama pull qwen3:8b`.

### Ausführen (immer aus dem Projekt-Root)

```bash
source venv/bin/activate
python -m tests.scripts.test_agent              # Agent mit Tool-Beispiel
python -m tests.scripts.test_skills             # Skills laden + Agent-Run
python -m tests.scripts.test_runtime            # RuntimeEnv-Beispiel
python -m tests.scripts.test_function_call      # scannt alle lokalen Modelle auf Tool-Call-Support
python -m tests.scripts.test_agent_session      # AgentSession mit Read-Only-Tools
python -m tests.scripts.test_agent_session_chat # interaktiver Chat (fortsetzt letzte Session)
python -m tests.scripts.test_bootstrap_chat     # Bootstrap-Chat über die Templates
```

Interaktive Skripte (`test_agent_session_chat`, `test_bootstrap_chat`) beenden mit `/bye`.

### Webchat/`gateway`-Skripte

```bash
# 1. Gateway starten (andere Shell):
source venv/bin/activate
python -m selma.main

# 2. Stream-Client:
python -m tests.scripts.test_webchat
```

`test_webchat.py` erwartet ein Gateway unter `http://localhost:8000` (URL oben im Skript anpassbar).

### Hinweise

- `test_function_call.py` akzeptiert `--base-url` und `--timeout` (Default: `http://localhost/v1` — vermutlich Typo im Projekt, bei Bedarf anpassen).
- Einige Skripte (`test_bootstrap_chat`) kopieren `templates/*.md` in den Workspace — danach Workspace prüfen und ggf. `git checkout .` falls nicht gewünscht.
- Skripte sind **reihenfolge-unabhängig** und idempotent (Ausnahme: Bootstrap kopiert Dateien, Chat-Skripte schreiben Sessions).
