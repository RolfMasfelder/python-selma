# Refactoring-Plan: `runtime.py` → `runtime/`-Paket (P3 #13)

Path: `REFACTOR_RUNTIME_PLAN.md` — Ersatz für REFACTOR_TODO-#13 (2026-11-15 auf Rolf-Auftrag neu gestartet).

## Ziel

`src/selma/runtime.py` ist eine einzige Datei mit **1285 Zeilen** (God-Module; P1–P2-Fixes haben sie sogar *größer* gemacht). Ziel-Muster = das bereits für `my_tools` eingesetzte **Facade-Muster**:

```
src/selma/runtime/
├── __init__.py    # Fassade: re-exportiert die gesamte bisherige öffentliche API
├── common.py      # L0: geteilte Datenmodelle + Blocker-Chunker + Lifecycle-Bus
├── command.py     # L1: agent_command (Orchestrierung, "Who/Where/With what")
└── attempt.py     # L2+L3: run_embedded_pi_agent / run_embedded_attempt (Retry-Loop)
```

**Harte Regeln:**

- **Keine Behavior-Änderung.** Code wird nur *umsortiert*; Signatur, Exception-Typ/-Message, Log/Trace-Reihenfolge bleiben identisch.
- **Consumer-API 100 % stabil:** jeder Import der Form `from selma.runtime import X` und jeder `selma.runtime.X`-Attributezugriff (Produktion + Tests, inkl. `sys.modules["selma.runtime"]`-Mocks) muss ohne Änderung funktionieren.
- **Ein Schritt = eine Datei-Auslagerung** + Fassade-Update + Tests + Status hier eintragen. Nach JEDEM Schritt: `pytest tests/unit tests/integration -q` grün + `ruff check` + `ruff format --check` + `mypy src/selma` → erst dann Status "✅ erledigt".

## Abhängigkeitsgraph (AST-analysiert 2026-11-15)

```
common  ──(keine interne Abhängigkeit)
command ──> common, attempt
attempt ──> common
           └── deferred import von command.memory_flush
```

`command.py` importiert die Attempt-Funktionen auf Modulebene. Deshalb darf `attempt.py` `command.py` **nicht auf Modulebene** importieren. `repair_context_overflow` lädt `memory_flush` erst innerhalb der Funktion aus `command`; zu diesem Zeitpunkt ist die Package-Initialisierung abgeschlossen. Das erzeugt keinen Import-Zyklus beim Laden der Module. Der verschachtelte Agent-Turn von `memory_flush` während der Overflow-Reparatur ist beabsichtigtes Laufzeitverhalten.

### Memory-Funktionen: Zuständigkeit und Modulgrenze

- Die einzige `memory_*`-Funktion in `runtime.py` ist `memory_flush`. Sie startet über `agent_command` einen stillen Agent-Turn und gehört daher zu `runtime/command.py`.
- `memory_get` und `memory_search` sind Tool-Namen; die Builder `make_memory_get_tool` und `make_memory_search_tool` liegen in `selma/tools.py`. Der Suchindex liegt bereits separat in `selma/memory_index.py`.
- Diese Tool- und Indexfunktionen werden **nicht** nach `runtime/memory.py` verschoben: Sie haben andere Zuständigkeiten und sollen nicht mit der Runtime-Orchestrierung gekoppelt werden. Ein separates Runtime-Memory-Modul nur für `memory_flush` würde hier keine sinnvolle gemeinsame Abstraktion bilden.
- `attempt.py` ruft `memory_flush` in `repair_context_overflow` per lokalem Import aus `selma.runtime.command` auf. Es gibt keinen Top-Level-Import von `command` in `attempt.py`. Nach der Auslagerung müssen die Unit-Tests ihre Monkeypatches auf das definierende Modul `selma.runtime.command` ausrichten; der öffentliche Import über `selma.runtime` bleibt unverändert.

### Verzeichnis-Zugehörigkeit (endgültig)

| Ziel | Symbole (vollständig) |
|---|---|
| `runtime/common.py` | `BlockReplyChunkingConfig`, `DeliveryContext`, `RuntimeEnv`, `RunPayload`, `AgentRunMeta`, `AgentCommandResult`, `LifecyclePhase`, `_BlockChunker`, `_LIFECYCLE_LISTENERS`, `on_lifecycle_event`, `emit_lifecycle_event` |
| `runtime/command.py` | `_resolve_skills_snapshot`, `get_session`, `detect_bootstrap_mode`, `CommandContext`, `_prepare_command`, `_build_run_options`, `_execute_command`, `_finalize_command`, `agent_command`, `memory_flush` |
| `runtime/attempt.py` | `AttemptError`, `AttemptResult`, `MAX_OVERFLOW_COMPACTION_ATTEMPTS`, `LoopState`, `pick_fallback_thinking_level`, `handle_aborted`, `repair_thinking_not_supported`, `repair_context_overflow`, `_error_result`, `RunEmbeddedPiAgentOptions`, `run_embedded_pi_agent`, `CollectedOutput`, `PromptResult`, `_load_context_files`, `_THINKING_LEVELS`, `_extract_rejected_level`, `detect_attempt_error`, `subscribe_output_collector`, `execute_prompt`, `build_attempt_result`, `_build_runtime_info`, `_build_attempt_system_prompt`, `_resolve_effective_prompt`, `_resolve_active_tools`, `_create_attempt_session`, `run_embedded_attempt` |
| `runtime/__init__.py` | Re-Export ALLES Oben (stabile Fassade) |

### Consumer die NICHT angefasst werden (alle funktionieren über Fassade)

`adapter_telegram` `adapter_webchat` `channel_adapter` `command_manager` (`memory_flush` Import!) `delivery` (TYPE_CHECKING) `gateway` `heartbeat` (deferred import) + ≥ 8 Test-Dateien.

**Test-Isolations-Falle (bekannt, Stolperstein #4):** `test_unit_heartbeat_async.py` tauscht `sys.modules["selma.runtime"]` gegen ein `types.ModuleType("selma.runtime")`-Fake → funktioniert, weil die Fassade ein echtes reguläres Modul bleibt. Kein Test referenziert `selma.runtime._Privates` (grep verifiziert: 0 Treffer außerhalb `test_unit_runtime.py`, und die referenziert sie WIE AUCH IMMER über `rt.` = `selma.runtime`-Modul).

## Schritte

> Status-Semantik: `☐ offen` → `… in Arbeit` → `✅ erledigt HH:MM` (Datum davor) / `⛔ Blockiert: Grund`.
> Jeder Schritt endet mit: **Status:** ✅ + Ergebnis-Tests-Zeile.

### Schritt 0 — Baseline & Vorbedingung prüfen
☐ Status (ausgefüllt):
- [x] `pytest tests/unit tests/integration -q` → **799 passed**
- [x] `ruff check src/selma tests` → All checks passed!
- [x] `ruff format --check src/selma tests` → 77 files already formatted
- [x] `mypy src/selma` → Success: no issues found in 36 source files
- [x] `find ~/.selma -type f | wc -l` → **0**
- [x] `git status --short` → nur `M pyproject.toml`, `M src/selma/__init__.py` (0.2.1, Rolf-uncommitted)
- [x] Kein Test greift auf `selma.runtime.<private>` oder `selma.runtime.*`-Attribute zu, die NICHT in der Fassade landen → **verifiziert** (einzige privaten-Ref: `rt._BlockChunker` in `test_unit_runtime.py:165-200` — `common._BlockChunker` wird von `runtime/__init__.py` unter exakt demselben Namen re-exportiert → `rt._BlockChunker` bleibt erreichbar. ✅)
**Status:** ✅ 2026-11-15

### Schritt 1 — `runtime/common.py` auslagern (die 11 L0-Symbole)
- [x] **Package zuerst herstellen:** `src/selma/runtime.py` nach `src/selma/runtime/__init__.py` überführen. Es darf nicht gleichzeitig eine `runtime.py`-Moduldatei geben, die `selma.runtime.common` verdeckt. Die bestehende Runtime-Implementierung bleibt zunächst in `__init__.py`, damit alle bisherigen Consumer-Imports weiter funktionieren.
- [x] `src/selma/runtime/common.py` anlegen bzw. vervollständigen: Header, benötigte Imports (`Any, Callable, Literal`; `BaseModel, Field`; `trace_and_log`; `logging`), die sieben Datenmodelle, `_BlockChunker`, `_LIFECYCLE_LISTENERS`, `on_lifecycle_event` und `emit_lifecycle_event`.
- [x] Die ausgelagerten Definitionen aus `runtime/__init__.py` entfernen und dort **vor** ihrer Verwendung aus `selma.runtime.common` re-exportieren (alle 11 Namen). Keine doppelten Klassen-/Funktionsdefinitionen in der Fassade stehen lassen.
- [x] Verifikation: `python -c "from selma.runtime import DeliveryContext, AgentCommandResult, _BlockChunker"` muss funktionieren. `python -c "import selma.runtime as r; print(r.__file__, r.DeliveryContext.__module__)"` muss die Package-Fassade und `selma.runtime.common` ausgeben.
- [x] **Tests:** `pytest tests/unit tests/integration -q` grün; `ruff check src/selma tests` + `ruff format --check src/selma tests` + `mypy src/selma`.
- **Status:** ✅ erledigt 2026-11-18 — Package-Import-Smoke-Test und finale Prüfungen: 801 Tests bestanden, Ruff grün, Mypy 0 Fehler.

### Schritt 2 — `runtime/attempt.py` auslagern (alle L2/L3-Symbole)
- [x] `src/selma/runtime/attempt.py` anlegen: Header, benötigte Imports aus `selma.config`, `selma.compaction`, `selma.session_store`, `selma.system_prompt`, `selma.tools`, `selma.tracing`, `selma.helper`, `selma.resource_loader`, `selma.agent`, `selma.agent_session`, `pydantic`, `typing` und `runtime.common` (die 6 tatsächlich verwendeten L0-Symbole: `AgentCommandResult`, `AgentRunMeta`, `BlockReplyChunkingConfig`, `DeliveryContext`, `RunPayload`, `_BlockChunker`).
- [x] `attempt.py` importiert `memory_flush` in Schritt 2 zunächst **innerhalb** von `repair_context_overflow` aus der Package-Fassade (`selma.runtime`), da `command.py` erst in Schritt 3 ausgelagert wird. Kein Top-Level-Import von Layer-1-Symbolen.
- [x] Die ausgelagerten Definitionen aus `runtime/__init__.py` entfernen und die Attempt-Symbole dort explizit aus `selma.runtime.attempt` re-exportieren. `agent_command` und `memory_flush` verbleiben bis Schritt 3 in der Fassade/Monolith-Implementierung.
- [x] Verifikation: `python -c "from selma.runtime import agent_command, run_embedded_attempt, detect_attempt_error, RunEmbeddedPiAgentOptions"`; `python -c "import selma.runtime as r; r.RunEmbeddedPiAgentOptions.__module__, r.run_embedded_attempt.__module__"` → beide `selma.runtime.attempt`. `agent_command.__module__` → `selma.runtime.command` (nach Schritt 3) ODER `selma.runtime` (jetzt, in Schritt 2 noch vor Fassade).
- [x] **Tests:** vollständige Suite grün + Ruff + Mypy. **Fokustest** (`tests/unit/test_unit_runtime_imports.py`): jedes `__all__`-Symbol ist vorhanden und zentrale Re-Exports sind identische Objekte aus ihren Layer-Modulen.
- **Status:** ✅ erledigt 2026-11-18 — Finale Suite 801 Tests bestanden; Runtime-Fokustests 88 bestanden.

### Schritt 3 — `runtime/command.py` auslagern (alle L1-Symbole)
- [x] `src/selma/runtime/command.py` anlegen: Header und benötigte Imports aus den vorhandenen Selma-Modulen, `runtime.common` und `runtime.attempt`; `agent_command` und `memory_flush` gehören beide hierher. `command.py` darf `attempt` auf Modulebene importieren.
- [x] `memory_flush` ruft `agent_command` direkt auf, da beide nach diesem Schritt in `command.py` liegen. In `attempt.py` den bisherigen lokalen Fassaden-Import in `repair_context_overflow` durch `from selma.runtime.command import memory_flush` ersetzen. Dieser Import bleibt **innerhalb der Funktion**; `attempt.py` erhält keinen Top-Level-Import von `command`.
- [x] Die L1-Definitionen aus `runtime/__init__.py` entfernen und die Symbole explizit aus `selma.runtime.command` re-exportieren, einschließlich `memory_flush`.
- [x] Tests, die `rt.memory_flush` oder `rt.agent_command` monkeypatchen, so anpassen, dass sie die Funktionen im definierenden Modul `selma.runtime.command` patchen. Die bestehenden öffentlichen Importe über `selma.runtime` bleiben unverändert.
- [x] Verifikation: `python -c "import selma.runtime as r; print(r.agent_command.__module__, r.memory_flush.__module__, r.CommandContext.__module__)"` → jeweils `selma.runtime.command`. Import-Smoke-Test für `selma.runtime.command` und `selma.runtime.attempt`; kein Top-Level-Import von `selma.runtime.command` in `attempt.py`.
- [x] **Tests:** vollständige Suite grün + Ruff + Mypy. Kein Laufzeitzustand im Workspace wird durch den Refactor geschrieben.
- **Status:** ✅ erledigt 2026-11-18 — Finale Suite 801 Tests bestanden; Ruff grün; Mypy 0 Fehler.

### Schritt 4 — Fassade `runtime/__init__.py` abschließen
- [x] Die Package-Umstellung ist bereits Voraussetzung aus Schritt 1. `runtime/__init__.py` enthält jetzt nur noch explizite Re-Exporte und gegebenenfalls die bisher benötigte Modul-Metadaten-Kompatibilität; es gibt keine alternative `runtime.py`-Datei.
- [x] `runtime/__init__.py` exportiert die Module und Symbole `common`, `attempt` und `command` mit einer vollständigen, expliziten `__all__`-Liste. Kein `import *`.
- [x] **Kritischer Test für `__all__`:** `python -c "import selma.runtime as r; missing = [n for n in r.__all__ if not hasattr(r, n)]; assert not missing, missing"` → jede Nennung in `__all__` ist ein echtes, importierbares Attribut.
- [x] **Tests:** vollständige Suite + `ruff` + `mypy` + **`python -c "import selma.runtime as r; assert r.DeliveryContext is selma.runtime.common.DeliveryContext"` (identische Objekt-Identität, Fassade ≠ Kopie!). `r.command.agent_command is r.agent_command` (Command-Facade → Command-Objekt).
- **Status:** ✅ erledigt 2026-11-18 — Package-Fassade mit expliziter `__all__`; Import- und Objektidentitätstests grün.

### Schritt 5 — Abschluss & Dokumentation
- [x] Paketgrößen geprüft: `common.py` 191 Zeilen, `command.py` 404, `attempt.py` 692 und Fassade `__init__.py` 114.
- [x] **KEINE Runtime-Definitionen mehr in `runtime/__init__.py`** (nur Re-Exporte, `logger` und `__all__`).
- [x] **REFACTOR_TODO.md #13** mit ✅, Verifikation und Pointer auf `REFACTOR_RUNTIME_PLAN.md` aktualisiert.
- [x] **MEMORY.md** und Tagesnotiz aktualisiert; Änderungen sind noch nicht committed, daher wird kein Commit-Hash vorgetäuscht. Nach dem Commit den Hash ergänzen.
- [x] **Rollback-Hinweis:** Änderungen nur gezielt anhand des aktuellen `git status` und der tatsächlich eigenen Patches zurücknehmen. Kein pauschales `git checkout`, kein rekursives Entfernen des Runtime-Pakets und kein `git reset --hard`.
- [x] **Nicht-umgesetztes (bewusst):** kein `from __future__ import annotations` entfernt, kein `import *` (F403); die Fassade verwendet explizite Exporte.
- **Status:** ✅ erledigt 2026-11-18 — `REFACTOR_TODO.md`, `MEMORY.md` und die Tagesnotiz aktualisiert. Nicht committed; Commit-Verweis nach dem Commit ergänzen.

## Offene / verbleibende Fragen
- **Modul-Zyklus:** entschieden. `memory_flush` liegt in `command.py`; `attempt.py` importiert es nur innerhalb von `repair_context_overflow`. Die Schrittfolge stellt sicher, dass der Rückimport erst nach abgeschlossener Package-Initialisierung erfolgt.
- **Memory-Modul:** kein neues `runtime/memory.py`. `memory_get`/`memory_search` bleiben bei den Tool-Buildern in `tools.py`, der Suchindex in `memory_index.py`; `memory_flush` bleibt bei der Command-Orchestrierung.
- **`__all__` in der Fassade:** vollständige, explizite Liste — **KEIN** `*` (F403). Die Fassade enthält 47 Layer-Symbole (11 common, 10 command, 26 attempt), die drei Layer-Module und den zuvor über `selma.runtime` sichtbaren `EmbeddedContextFile`-Kompatibilitätsexport.
