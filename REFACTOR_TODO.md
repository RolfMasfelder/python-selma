# REFACTOR_TODO.md — Code-Smells in `src/selma/`

Aufgezeichnet: 2026-09-15 (Selma, nach Smell-Review auf Rols Anstoß).
Ziel: Schritt für Schritt abarbeiten. **Nach JEDER Aufgabe:** `ruff check` + `ruff format --check` +
komplette Suite (`tests/unit tests/integration`, Coverage soll ≥ Vorher-Stand bleiben, aktuell 98 %).
Vorheriger Stand: 723 passed, alle Files ≥ 88 %.

Vorgaben (wie bei den Coverage-Runden):
- `source venv/bin/activate` vor allem Python
- Tests isoliert halten (autouse `SELMA_STATE_DIR`-Fixture, `find ~/.selma -type f` → 0)
- Kein pytest-asyncio, sync-Loop-`run(coro)`-Stil pflegen
- Vor Commit: `git status` + `git log --oneline` checken; keine Co-Authored-By-Trailer
- mypy war 2026-09-11 Projekt-CLEAN (`mypy src/selma` → 0 errors) — nach jedem Refactor-Re-Run

---

## P1 — Kleine, lokal begrenzte Fixes (je ~1 Runde)

### 1. ~~`heartbeat.py:254` —`global next_heartbeat_at`~~ ✅ **erledigt 2026-09-15**
- **Ursprünglicher Smell:** Hidden mutable module state + `global`-Statement (in diesem Repo sonst nirgends).
- **Consumer-Analyse (2026-09-15, vor dem Fix):**
  - EXTERNE Consumer: `command_manager.py:276` (liest für `/status`-Ausgabe) + `test_helper.py`-analoge Test-Stelle `tests/unit/test_unit_command_manager.py` (setzt vor `/status`).
  - INTERNE Consumer: `heartbeat_loop()` selbst (einziges `global`-Statement, gateway-launched Loop).
  - **Ergebnis:** ECHTE externe Consumer vorhanden → **Kapselung statt Verlokalisierung** (Local würde `command_manager` kaputt machen).
- **Umgesetzt (2026-09-15):** `next_heartbeat_at` → `_next_heartbeat_at` (privat), mit expliziten Accessors `get_next_heartbeat_at()`/`set_next_heartbeat_at()`; Consumer `command_manager.py` + `test_unit_command_manager.py` + `test_unit_heartbeat_async.py` umgestellt auf Accessor-API. `global`-Statement bleibt NUR im Setter (einziges im Repo neben `tracing.py:44`, P1#2). Begründung + Consumer-Nachweis in `memory/2026-09-15.md`.
- **Risk:** niedrig — eine Funktion + 1 externer Consumer, Suite 729 grün, heartbeat.py 100 % Coverage.

### 2. `tracing.py:44` — Modul-ebene Globalstate (`global otel_handler`)
- **Smell:** Wie #1, plus: `setup()` hat Modul-Ebenen-Side-Effects.
- **Fix:** Handler anstelle von Global in die Aufrufer-Konfiguration legen, oder explizite `get_handler()`/`set_handler()`-Methode an ein kleines Modul-Objekt.
- **Risk:** mittel (Aufrufer in `runtime.py`/`agent.py` anpassen) — erst Consumer greppen.

### 3. Breite-`except Exception`-Blöcke (34 Stellen, s. Liste unten)
- **Smell:** Bare `except Exception` + nur Log → Fehler-Typ-Info geht verloren; in Teilklassen eher `catch specific`.
- **NICHT blind ersetzen** — viele sind **bewusst broad** (z. B. `tools.py` Tool-Wrapper, die dem Agenten einen sauberen Fehler-String zurückgeben sollen).
- **Vorgehen je Stelle:** Entscheidung dokumentiert treffen — (a) specific-Exceptions, (b) broad mit Begründungs-Kommentar `# broad by design: …`.
- **Treffer-Liste:** `agent.py:277,311,384` · `agent_runtime.py:372` · `agent_session.py:606` · `command_manager.py:127` · `compaction.py:93,118` · `config.py:225` · `dashboard.py:101,200` · `gateway.py:119,138,179,208` · `heartbeat.py:80,276` · `memory_index.py:80,358,420` · `my_tools.py:709,767` · `runtime.py:250,448,629,984` · `session_store.py:194,208,250` · `setup.py:104` · `tools.py:45,89,209,287`

### 4. `typing.Any`-Überfluss (14 Stellen)
- **Smell:** `Any` als Flucht-Valve; hier sind die meisten **gerechtfertigt** (Event-Payloads, Channel-Adapter-Protokoll, `raw`-Fields), aber `agent.py:103 payload: Any = None` und `tracing.py:19 add_span_infos(**kwargs: Any)` sind die Kandidaten am ehesten enger typbar.
- **Vorgehen:** Nur Stellen angehen, wo ein konkretes Typed-Modell existiert und `Any` redundant ist. Liste:
  `agent.py:103,377` · `channel_adapter.py:21,24,26,27` (Protokoll — hier sind `Any` sinnvoll, ggf. `TypeVar`) · `config.py:79` (Pydantic-Validator, `Any` korrekt) · `data.py:21` · `task_manager.py:20` · `tracing.py:19,25`
- **Risk:** niedrig.

### 5. `system_prompt.py:575 build_agent_system_prompt(params)` — **187 Zeilen**
- **Smell:** Long function + Feature envy (liest an 10+ parametern aus `params` bzw. Sections).
- **Fix:** Bereits teil-sektional aufgebaut (`_build_reaction_section` etc.) — restliche Inline-Blöcke > 10 Zeilen zu `_build_*_section()`-Helpers extrahieren.
- **Risk:** mittel. Coverage `system_prompt.py` 100 % halten (Tests greifen auf `build_agent_system_prompt` Ergebnis → internale Extraktion sollte transparent bleiben).

---

## P2 — Mittlere Refactors (je 1-2 Runden)

### 6. `my_tools.py:451 make_grep_tool` — **213 Zeilen** (größte Funktion im Repo)
- **Smell:** Long function + Duplicated logic (rg-Branch und Python-Fallback teilen ~40 %).
- **Fix:** Gemeinsame Post-Processing-Schleife (Truncation, `limit`, Byte-Budget) in `_format_matches(lines, …)` extrahieren; die `execute(path, …)`-Innere in 2 Pfade + 1 Shared-Formatter.
- **ACHTUNG:** Stolperstein #12 (rg-branch `abs:LINE:content` vs Python `rel:LINE: content`) — Form-Tests sind dafür da, gerade **diesen** Unterschied fixieren; beim Refactor nicht "vereinheitlichen".
- **Coverage:** `my_tools.py` 100 % halten (50+ Tests) — Refactor muss behavior-preserving sein.

### 7. `agent.py:150 _run_loop` — **135 Zeilen**
- **Smell:** Long function + Divergent change (ein Loop, mehrere Zustandsübergänge).
- **Fix:** Turn-Phasen (Prompt → Tool-Call-Prüfung → Antwort) in `_handle_turn(...)`/`_handle_tool_call(...)`-Methoden zerlegen, solange das `message_end`-Timing (Stolperstein #7!) nicht bricht.
- **ACHTUNG:** `message_end`-Events müssen WÄHREND `prompt()` laufen — Refactor darf den Send-Punkt nicht in eine "sauberere" Position nach `prompt()` verschieben.
- **Coverage:** `agent.py` 100 % halten.

### 8. `agent_session.py:570 create_agent_session(options)` — **120 Zeilen**
- **Smell:** Long function + Excessive parameter list (der `options`-Block ist eine Pydantic-Klasse — schon gut), aber die Funktion selbst vermischt 3 Aufgaben: Session-File-Setup, Agent-Konstruktion, Tool-Registrierung.
- **Fix:** `_init_session_files(...)`, `_build_agent(...)`, `_register_tools(...)` extrahieren.
- **ACHTUNG:** Stolperstein (agent_session-Runde): `session_id` vs `session_file.stem`, `spawn_background_task`-Timing, `cwd`-Default — Tests sind darauf abgestimmt.
- **Coverage:** `agent_session.py` 99 % halten (45 Tests).

### 9. `runtime.py:330 agent_command(...)` — **137 Zeilen, 6 Parameter**
- **Smell:** Long function + Excessive parameter list (6 Parameter, mehrere optional) + Divergent change (der zentrale Dispatch).
- **Fix:** Parameter zu einem `CommandContext`-Datensatz (Pydantic oder simple dataclass); die 3 Phasen (Session-lookup → Prompt → Delivery) in Helper-Methoden.
- **Risk:** mittel-hoch — `agent_command` ist der Entry-Point für alle Commands.
- **VORHER:** `mypy` auf diesem File re-runnen (war 2026-09-09 manuell auf 0 gebracht; Refactor darf da keine neuen `arg-type`/`no-redef`-Fehler bringen).

### 10. `runtime.py:1041 run_embedded_attempt(opts)` — **94 Zeilen**
- **Smell:** Long function + Feature envy (`opts`, `session`, `tools`, `delivery` …).
- **Fix:** Fallback-Kaskade (Stolperstein: `reject_thinking`-Erwartung!) in `_choose_model(...)`; Attempt-Bau in `_build_attempt(...)`.
- **ACHTUNG:** `run_embedded_attempt` wird **AWAITED** — Async-Fakes in Tests müssen echt `async def` sein (PEP-479, Stolperstein runtime-Runde).

### 11. `tools.py:140 make_browser_tool` — **117 Zeilen** (mit 9-Param-`execute`)
- **Smell:** Long function + Excessive parameter list (9 Parameter in `execute`!).
- **Fix:** Parameter in einen `BrowserParams`-Datensatz; `execute`-Body in `if action in (...)`-Ketten statt lange `if/elif`.
- **Coverage:** `tools.py` 99 % halten.

### 12. `memory_index.py:305 _hybrid_search(...)` — **83 Zeilen, 5-Param**
- **Smell:** Long function + 5 Parameter (inkl. `mtime_by_path`-Optional, das 2026-09-09 ein echtes Bug-Trigger hat).
- **Fix:** FTS-Stage, Vec-Stage, Rerank in 3 Methoden; `mtime_by_path: dict[str,float] | None = None` als Teil eines `_SearchContext`-Objekts.
- **ACHTUNG:** Call-Zählung von `_connect` (Stolperstein memory_index-Runde) — Tests patchen an Position 1/2, Refactor darf Call-Reihenfolge NICHT ändern.
- **Coverage:** `memory_index.py` 100 % halten.

---

## P3 — Größere / strukturelle Refactors (mehrerige Runden)

### 13. `runtime.py` (1134 Zeilen, 99 % Coverage) — God-Module-Kandidat
- **Smell:** Datei ist das "Zentralwerk" mit 10+ Funktionen > 40 Zeilen (`agent_command`, `run_embedded_attempt`, `detect_attempt_error`, `execute_prompt`, …).
- **Fix (optional, erst wenn P1-P2 rum sind):** Aufspaltung in `runtime/session.py`, `runtime/attempt.py`, `runtime/error_detect.py`; `runtime.py` als Fassade/Re-Export.
- **Risk:** **hoch** — viele Import-Stellen, `selma.runtime` in sys.modules-Mocks (Stolperstein #6).
- **Nur mit Rolf-Grünlicht**, wenn nach P1-P2 das Gefühl besteht "es wird dicker".

### 14. Data-Cluster: `session_key + session_id`-Paar wird durch 5+ Stellen weitergereicht
- **Smell:** Data clumps (siehe `agent_command`, `resolve_session`, `reset_session`, `agent_runtime._execute`).
- **Fix:** Ggf. `SessionRef(session_key, session_id)`-Datensatz einführen, wenn nach P2 klar ist, dass es sich häuft.
- **VORHER:** `grep -rn 'session_key.*session_id' src/selma | wc -l` zählt aktuelle Ausdehnung.

### 15. `my_tools.py` 874 Zeilen — Modul-Struktur
- **Smell:** 8 separate `make_*_tool`-Werkzeuge + Helpers in einer Datei; nicht unbedingt ein Problem, aber bei #6 + #11 (tools.py-Browser) wächst der Code.
- **Fix (optional):** `my_tools/grep.py`, `my_tools/exec.py`, `my_tools/find.py` … — **NUR wenn #6 + #11 tatsächlich unhandlich werden**.

### 16. `dashboard.py:81 settings_dialog()` — 56 Zeilen, 2× `except Exception` (L101, L200)
- **Smell:** Feature envy + broad except.
- **Fix:** UI-Texte in String-Konstanten, Dialog-Logik in 3-4 Funktionen.
- **Priorität:** niedrig (UI, wenig test-covered vermutlich) — erst #3 (breite-except-Liste) abarbeiten.

---

## Abnahme-Kriterien (pro Aufgabe)

- [ ] `ruff check` grün, `ruff format --check` grün
- [ ] `mypy src/selma` → 0 errors (vorher 0 — nicht verschlechtern)
- [ ] Voll-Suite `--cov` ≥ zuvor (98 % aktuell, keine Datei < 88 %)
- [ ] `find ~/.selma -type f` → 0
- [ ] Commit-Message: kurze Beschreibung + betroffenes File, **kein** Co-Authored-By-Trailer
- [ ] `git status` + `git log --oneline` checken VOR "fertig-sagen"
- [ ] Memory (MEMORY.md + memory/YYYY-MM-DD.md via `date`!) aktualisieren

## Offene Fragen an Rolf (vor Start)

1. Reihenfolge: Top-down durch P1→P3 oder bestimmte Files zuerst?
2. P1 #3 (breite-except): soll ich für jeden Treffer eine Begründungs-Kommentar-Variante vorschlagen, oder einfach bulk specificize wo klar?
3. P2 #7 (`_run_loop`) und #9 (`agent_command`) berühren den heikelsten Code (message_end-Timing) — soll ich da **erst** eine zusätzliche regression-Test-Safety-Net-Session machen, bevor ich anfange?
