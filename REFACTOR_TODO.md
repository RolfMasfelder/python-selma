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

### 2. ~~`tracing.py:44` — Modul-ebene Globalstate (`global otel_handler`)~~ ✅ **erledigt 2026-09-15**
- **Consumer-Analyse:** `test_helper.py:19-20` (liest für `setup_logger`), `tests/unit/test_unit_tracing.py` (liest + setzt Cleanup), `tests/unit/test_unit_test_helper.py` (patcht vor `setup_logger`). Keine `runtime.py`/`agent.py`-Consumer — die TODO-Angabe war konservativ, Grep hat sie widerlegt.
- **Umgesetzt:** `otel_handler` → `_otel_handler` (privat) + Accessors `get_otel_handler()`/`set_otel_handler()`; `global` NUR im setter. `setup()` benutzt jetzt einen lokalen `handler` + `set_otel_handler(handler)` (kommentierter Block weg). Suite 729 grün, tracing.py 89 %, ruff/mypy grün.
- **Global-Bestand danach:** genau 1 `global`-Statement im Repo — `heartbeat.py:48` (im P1#1-Setter, dokumentiert + begründet).

### 3. Breite-`except Exception`-Blöcke (34 Stellen, s. Liste unten)
- **Smell:** Bare `except Exception` + nur Log → Fehler-Typ-Info geht verloren; in Teilklassen eher `catch specific`.
- **NICHT blind ersetzen** — viele sind **bewusst broad** (z. B. `tools.py` Tool-Wrapper, die dem Agenten einen sauberen Fehler-String zurückgeben sollen).
- **Vorgehen je Stelle:** Entscheidung dokumentiert treffen — (a) specific-Exceptions, (b) broad mit Begründungs-Kommentar `# broad by design: …`.
- **Treffer-Liste:** `agent.py:277,311,384` · `agent_runtime.py:372` · `agent_session.py:606` · `command_manager.py:127` · `compaction.py:93,118` · `config.py:225` · `dashboard.py:101,200` · `gateway.py:119,138,179,208` · `heartbeat.py:80,276` · `memory_index.py:80,358,420` · `my_tools.py:709,767` · `runtime.py:250,448,629,984` · `session_store.py:194,208,250` · `setup.py:104` · `tools.py:45,89,209,287`

### 4. ~~`typing.Any`-Überfluss (14 Stellen)~~ ✅ **erledigt 2026-09-15**
- **Smell:** `Any` als Flucht-Valve; hier sind die meisten **gerechtfertigt** (Event-Payloads, Channel-Adapter-Protokoll, `raw`-Fields), aber `agent.py:103 payload: Any = None` und `tracing.py:19 add_span_infos(**kwargs: Any)` sind die Kandidaten am ehesten enger typbar.
- **Vorgehen:** Nur Stellen angehen, wo ein konkretes Typed-Modell existiert und `Any` redundant ist. Liste:
  `agent.py:103,377` · `channel_adapter.py:21,24,26,27` (Protokoll — hier sind `Any` sinnvoll, ggf. `TypeVar`) · `config.py:79` (Pydantic-Validator, `Any` korrekt) · `data.py:21` · `task_manager.py:20` · `tracing.py:19,25`
- **Risk:** niedrig.
- **Umsetzung (2026-09-15, Commit `0eb709b`):** `AgentEventPayload = AgentMessage | ToolCallRequest | str | None` (alias + Kommentar-Mappe je Event-Typ) in agent.py; `SpanAttributeValue = float | bool | int | str | None` + None-Skip in tracing.py; `is_enabled/start(config: SelmaConfig)` in channel_adapter.py. Consumer-Narrowing: agent_runtime.py EventSubscriber auf `isinstance`-Checks, agent_session.py `_on_agent_event` mit `assert isinstance(…, AssistantMessage)` (Statt-Falsy-Tests). Tests: SimpleNamespace-Payload-Fakes → echte Pydantic-Modelle (AssistantMessage/ToolCallRequest) in test_unit_agent_runtime.py + test_unit_runtime.py (Stolperstein #7 — Pydantic-Modelle nicht mit SimpleNamespace fake-en). Restliche `Any`=4 Stellen: dokumentiert begründet (`raw: Any`-Protokoll, validate_tools_allow-Validator). Zahlen: **729 passed / 0 failed**, ruff+mypy grün, Gesamt-Coverage **99 %**, `find ~/.selma` → 0.

### 5. ~~`system_prompt.py: build_agent_system_prompt(params)` — **187 Zeilen**~~ ✅ **erledigt 2026-09-17**
- **Smell:** Long function + Feature envy (liest an 10+ parametern aus `params` bzw. Sections).
- **Fix:** 7 Inline-Blöcke → `_build_*_section`-Helper extrahiert (Tooling/Tool-Call-Style/Safety/Workspace/Silent-Replies/Runtime/Workspace-Files-Intro) → `build_agent_system_prompt` **187 → 114 Zeilen** (25Z Docstring; Rest reine Orchestrierung — kein Inline-Block > 10Z mehr).
- **Risk:** mittel → **verifiziert behavior-preserving:** Differential-Check vs. `git show HEAD:src/selma/system_prompt.py` über **25 Param-Combo-Cases (full/minimal/none, Reactions, Reasoning, Owner-Raw/Hash, Context-Files stable/dynamic/sanitize, Tools-Casing, …) → 25/25 BYTE-IDENTICAL**; `user_prompt_prefix` identisch. Suite 729 grün, `system_prompt.py` **100 %**, ruff/mypy grün.
- **Stolperstein 24 (neu):** Erste Edit-Runde auf Basis **falscher Datei-Struktur-Annahme** (vermutete Inline-Blöcke, die so nicht existierten) → 6 Non-Match-Editions + Helper-Namen-Kollision mit existierenden `_build_*`-Helpers (wäre Shadowing → Prompt-Änderung). **Regel: Vor großem Refactor IMMER realen Text per `grep -n "^def"` + `read` mitschneiden, Helper-Namen per ast-Grep auf Duplikate prüfen; bei Fehlstart `git status --short` + `git diff --stat` → `git checkout -- <eigene-datei>`, dann neu starten.**

---

## P2 — Mittlere Refactors (je 1-2 Runden)

### 6. ~~`my_tools.py:451 make_grep_tool` — **213 Zeilen** (größte Funktion im Repo)~~ ✅ **erledigt 2026-09-17**
- **Smell:** Long function + Duplicated logic (rg-Branch und Python-Fallback teilen ~40 %).
- **Fix (umgesetzt):** `make_grep_tool` → **213 → 91 Zeilen** (Docstring inkl., Body = Path-Resolution + Backend-Auswahl + Delegation). Extrahiert: `DEFAULT_GREP_LIMIT = 100`, `_grep_rg_available()`, `_grep_with_rg(…)->(lines, limit_reached)`, `_grep_with_python(…)`, `_format_grep_output(lines, limit_reached, effective_limit)` (gemeinsame Post-Processing: Truncation, byte-Budget, Notizen). Stolperstein #12 (rg vs python Output-Format) **bewusst NICHT vereinheitlicht** — beide Backends formen ihr Format unverändert weiter, nur das Post-Processing ist shared.
- **Verifikation:** Differential-Batterie **17 Cases × 3 Backend-Modi (py / rg / rg-Timeout) = 51 × BYTE-IDENTICAL** (modulo tmp-Dir-Pfade), deterministischer Re-Run. `test_unit_my_tools.py` 48 passed (nur Docstring-Referenz `_grep_python`→`_grep_with_python` aktualisiert). Suite **729 passed / 0 failed**, my_tools.py **100 %**, Gesamt 99 %, ruff+mypy grün, `~/.selma` → 0.

### 7. ~~`agent.py:150 _run_loop` — **135 Zeilen**~~ ✅ **erledigt 2026-09-17**
- **Smell:** Long function + Divergent change (ein Loop, mehrere Zustandsübergänge).
- **Fix (umgesetzt):** `_run_loop` → **135 → 83 Zeilen** (AST-verifiziert, keine Namen-Duplikate); Body = reine Orchestrierung (`turn_start`/`turn_end`, `message_end` + persist, Tool-Exec-Loop). Extrahiert: `async _stream_turn(openai_messages, openai_tools) -> (text_parts, tool_call_fragments)` (create()-Call inkl. `reasoning_effort` bei `thinking_level`, async-for-Chunk-Loop: `delta.content` → append + `message_update`-Emit, Tool-Call-Delta-Akku­mulation id/name/arguments-String-Concat pro index) und `@staticmethod _parse_tool_calls(fragments) -> list[ToolCallRequest]` (`json.loads`, `JSONDecodeError` → `{}`-Fallback).
- **Stolperstein #7 eingehalten:** `message_end` wird weiterhin WÄHREND `prompt()` emittiert (im Loop vor Return), `message_update` während des Streams — zusätzlich jetzt mit explicitem Regressionstest `test_message_end_fires_while_prompt_task_still_running` (assertet `task.done() is False` beim message_end-Ereignis).
- **Verifikation:** Differential-Check vs. `git show HEAD:src/selma/agent.py` (scenarios plain / thinking / convert_to_llm-Hook) → **BYTE-IDENTICAL** (Events, create()-kwargs, resultierende Messages). Suite **730 passed / 0 failed** (729 + 1 neuer Test), `agent.py` **100 %**, Gesamt **99 %**, ruff check+format grün, mypy **0 errors / 30 files**, `find ~/.selma -type f` → 0.

### 8. ~~`agent_session.py:570 create_agent_session(options)` — **120 Zeilen**~~ ✅ **erledigt 2026-09-19** (Commit `cf692c4`)
- **Smell:** Long function + Excessive parameter list (der `options`-Block ist eine Pydantic-Klasse — schon gut), aber die Funktion selbst vermischt 3 Aufgaben: Session-File-Setup, Agent-Konstruktion, Tool-Registrierung.
- **Fix (umgesetzt):** `create_agent_session` → **120 → 56 Zeilen** (incl. 18Z Docstring; Body 38 Z = reine Orchestrierung, 0 Inline-Blöcke > 10 Z, AST-verifiziert, 0 doppelte private Modul-Namen). 6 neue **Modul-Level**-Helper: `_resolve_session_manager(options)`, `async _resolve_session_model(options)` (leeres Modell → `ModelRegistry().refresh()` + `get_available()[0]`), `_build_session_tools(options)` (custom Tools or `create_coding_tools()`), `_build_session_system_prompt(options, context_files, tools)` (my_system_prompt-Default + `reasoning_tag_hint` je `thinking_level`), `_build_session_agent(model, tools, system_prompt, options)` (Agent-Konstr + `state.session_files`), `_restore_or_init_history(agent, options, session_manager, model, system_prompt)` (`continue_session`: letztes `MessageEntry` nachladen + `restore_from_entries`, sonst `initialize_new_session` MetaEntry).
- **Modul-Ebene bewusst:** Tests patchen `AS.ModelRegistry` / `AS.create_coding_tools` (nicht auf `AS.create_agent_session`-Level) — Helper müssen auf `AS.*` referenzierbar sein.
- **ACHTUNG (eingehalten):** Stolperstein (agent_session-Runde): `session_id` (36 Z) ≠ `session_file.stem` (8 Z), `cwd`-Default nur wenn in Options gesetzt — Tests sind darauf abgestimmt.
- **Verifikation:** A/B-Differential **7 Cases** (default/think/registry/tools/ctx/hist/inmemory) **byte-identisch** gegen Altv via **in-process Dual-Load** (`git show HEAD:…` + `importlib`, shared Cwds, Noise-Norm `p28s_*`/jsonl-Names/ISO-Timestamps) — **NICHT** `git stash` (Stolperstein 25, Data-Loss-Risiko + alt-vs-alt-Fake-PASS). **Suite 730 passed / 0 failed** (62.5 s), `agent_session.py` **99 %** (318 stmts / 2 Miss L196-197, unverändert), Gesamt **99 %**, `agent.py`/`system_prompt.py` 100 %. `ruff check` + `ruff format --check` grün (90 Dateien), `mypy src/selma` **0 errors / 30 files**, `find ~/.selma -type f` → 0. `_scratch_p28/` geräumt (Stolperstein 26 — Ruff scannt das auch mit).
- Details: `memory/2026-09-19.md`.

### 9. ~~`runtime.py:330 agent_command(...)` — **137 Zeilen, 6 Parameter**~~ ✅ **erledigt 2026-09-20** (Commit `2d4fc60`)
- **Smell:** Long function + Excessive parameter list (6 Parameter, mehrere optional) + Divergent change (der zentrale Dispatch).
- **Fix (umgesetzt):** `agent_command` → **137 → 50 Zeilen** (AST-verifiziert, 0 doppelte private Modul-Namen). 5 neue Symbole (Modul-Ebene, `# -- Layer 1`-Block):
  - **`class CommandContext(BaseModel)`** — Daten-Carrier für die 6 Public-Parameter + alle abgeleiteten Werte. Nicht-optionaler Kern: `run_id, started_at, workspace_dir, delivery, config, store, session_record, is_new_session, session_file, provider, model, timeout_ms, tools_allow`; optional: `session_key, session_id, abort_signal` (mirror-run of `RunEmbeddedPiAgentOptions`); dazu `bootstrap_mode/thinking_level/skills_snapshot`. `model_config = {"arbitrary_types_allowed": True}` (asyncio.Event).
  - **`_prepare_command(...)`** (84 Z) — Phase 1: zuerst die Validierung (ValueError **vor** dem „start"-Event, Listener werden bei leerem message/key nicht berührt), dann `load_config`, `get_session`, `detect_bootstrap_mode`, `_resolve_skills_snapshot`, `save_session_store()`, Provider-Model-Default, Thinking-Resolve, `resolve_timeout`, `resolve_tools_allow` → `started_at` als **Local** in ctx.
  - **`_build_run_options(message, ctx) -> RunEmbeddedPiAgentOptions`** (22 Z) — reine Options-Assembly; Achtung: `session_key/session_id` existieren **nicht** in `RunEmbeddedPiAgentOptions` → werden **nicht** übergeben.
  - **`async _execute_command(...)`** (40 Z) — `lifecycle_ended`-Flag: im except „error"-Event **nur wenn `not lifecycle_ended`** (Original-Semantik), dann `logger.exception` + raise; Success → „end"-Event.
  - **`async _finalize_command(...)`** (11 Z) — `update_session_store_after_run(store=, session_record=, provider=, model=)` → **echtes `await deliver_result(result, ctx.delivery)`** (kein Fire-and-Forget!) → `return result`.
  - **`agent_command`** (50 Z, `@tracer.chain`) — `runtime or RuntimeEnv()`, `delivery or DeliveryContext()`, `workspace_dir = runtime.cwd` → `_prepare_command` → „start"-Event → `_build_run_options` → `await _execute_command` → `await _finalize_command`.
- **Behavior-Konservierung (Review-Korrekturen):** (1) Kein Fire-and-Forget beim Delivery (früher: `await None`-TypeError + `_DELIVERY_TASKS` undefiniert → jetzt echter `await`-Zweig, Verhalten original); (2) `session_key/session_id` aus dem Options-Call entfernt (existieren dort nicht); (3) Caller-Tuple-Unpacking entfernt; (4) `lifecycle_ended`-Guard im Error-Pfad wiederhergestellt.
- **Verifikation:** A/B-Differential **6 Cases** (Erfolg / leeres message / Key + ID missing / Layer2-Error / `sid`-only / thinking-Override) gegen `git show HEAD:`-Backup `/tmp/old_rt.py` per **in-process Dual-Load** (Stolperstein 25 — **niemals** `git stash`) → **6/6 OK** (Exception-Typ/-Message, Lifecycle-Events, update-Store-, Deliver-Call, Result). **Suite 730 passed / 0 failed** (62 s), `runtime.py` **99 %** (413 stmts / 3 Miss L955-957), Gesamt **99 %**. `ruff check` + `ruff format --check` grün (69 Dateien), `mypy src/selma` **0 errors / 30 files** (Aufforderung „mypy re-runnen" erfüllt — keine neuen `arg-type`/`no-redef`), `find ~/.selma -type f` → **0**. `_scratch_p29/` + `/tmp/old_rt.py` geräumt (Stolperstein 26).
- **Stolperstein 27 (neu → MEMORY.md): ECHTE Pydantic-Modelle in Fakes, wenn der Carrier validiert** — `CommandContext` validiert `config/store/session_record/skills_snapshot` gegen die echten Typen → `MagicMock`-Instanzen **verworfen**. Fix im A/B-Skript: echte, minimale Instanzen (`SelmaConfig()`, `SessionStore()`, `SessionRecord()`, `SkillsSnapshot(version=…)`) — beide rt-Module teilen dieselben importierten Klassen → gilt für alt UND neu.
- Details: `memory/2026-09-20.md`.

### 10. ~~`runtime.py:1041 run_embedded_attempt(opts)` — **94 Zeilen**~~ ✅ **erledigt 2026-09-21** (Commit `e39c825`)
- **Smell:** Long function + Feature envy (`opts`, `session`, `tools`, `delivery` …).
- **Fix (umgesetzt):** `run_embedded_attempt` → **94 → 44 Zeilen** (AST-verifiziert, 0 doppelte private Modul-Namen, 0 Inline-Blöcke > 10 Z; Body = reine Orchestrierung). 5 neue **Modul-Level**-Helper:
  - **`_build_runtime_info(opts)`** (15 Z) — `RuntimeInfo` (agent_id, host, model, default_model, os, arch, shell, channel aus `session_key.split(":")[-1]` wenn len ≥ 3).
  - **`_build_attempt_system_prompt(opts, runtime_info, context_files)`** (17 Z) — `build_agent_system_prompt(BuildAgentSystemPromptParams(…))` mit `workspace_dir=os.path.abspath(…)`, `tool_names=opts.tools_allow or ALL_TOOL_NAMES`, `skills_prompt`, `default_think_level`, `bootstrap_mode`.
  - **`_resolve_effective_prompt(opts)`** (8 Z) — Bootstrap-Präfix (`build_agent_user_prompt_prefix(bootstrap_mode) + "\n\n"`) **nur** wenn `opts.is_new_session`, sonst roher `opts.prompt`.
  - **`_resolve_active_tools(workspace_dir, opts)`** (9 Z) — `create_selma_tools()` + Filter auf `tools_allow` falls gesetzt.
  - **`async _create_attempt_session(system_prompt, tools, opts)`** (20 Z) — frische `AgentSessionManager(session_file=Path(opts.session_file))` + `await create_agent_session(CreateSessionOptions(…))`.
- **Hinweis:** Ursprünglich geplant war `_choose_model`/`_build_attempt` (Fallback-Kaskade) — die Fallback-Kaskade liegt aber in `run_embedded_pi_agent` (Layer 2), nicht hier; `run_embedded_attempt` ist der saubere Layer-3-Execution-Pfad → Aufspaltung entlang der echten Verantwortungen (Info/Prompt/Präfix/Tools/Session).
- **ACHTUNG (eingehalten, Stolperstein #5):** `run_embedded_attempt` wird **AWAITED** — A/B-Fakes (`execute_prompt`, `create_agent_session`) sind echt `async def`, nie `lambda`/sync.
- **Verifikation:** A/B-Differential **7 Cases** (`full` / `new_session_bootstrap` / `tools_allow` / `thinking` / `exec_raise` / `abort_signal` / `no_skills`) gegen `git show HEAD:src/selma/runtime.py` → `/tmp/old_rt.py` per **in-process Dual-Load** (**niemals** `git stash`, Stolperstein 25) → **7/7 OK** (Exception-Typ/-Message, normalisiertes Result, Event-Reihenfolge + Captured-Param-Sets). **Suite 730 passed / 0 failed** (62 s), `runtime.py` **99 %** (423 stmts / 3 Miss L955-957), Gesamt **99 %**. `ruff check` + `ruff format --check` grün (90 Dateien), `mypy src/selma` **0 errors / 30 files**, `find ~/.selma -type f` → 0. `_scratch_p210/` + `/tmp/old_rt.py` geräumt (Stolperstein 26/28 — destruk­tive Befehle isoliert).

### 11. ~~`tools.py:140 make_browser_tool` — **117 Zeilen**~~ ✅ **erledigt 2026-09-21**
- **Smell:** Long function + Excessive parameter list (9 Parameter in `execute`!).
- **Fix (umgesetzt):** `execute` **117 → 20 Z** (nur noch Signature + `BrowserParams`-Konstruktion + `_run_browser(cwd, params)`; `make_browser_tool`-Wrapper = Docstring + diese eine verschachtelte Funktion). Neue **Modul-Level**-Symbole (AST-verifiziert, 0 doppelte private Modul-Namen, 0 Inline-Blöcke > 10 Z):
  - **`@dataclass(frozen=True) class BrowserParams`** — 7 Felder (`url`, `action="extract"`, `selector/value/script/screenshot_path/wait_for` optional).
  - **`_page_text_truncated(page)`** — `body`-Text + Truncation-Note bei `_BROWSER_MAX_CHARS` (aus extract/click gehoben — gemeinsamer Pfad).
  - **5 Handler, einheitliche Signatur `(page: Any, cwd: str, params: BrowserParams) -> str`:** `_browser_extract`, `_browser_screenshot` (einziger `cwd`-Consumer: Default-Pfad), `_browser_click` (selector-Check + `wait_for_load_state`), `_browser_fill` (selector+value-Check), `_browser_evaluate` (script-Check).
  - **`_BROWSER_ACTIONS`-Tuple** (Name → Handler) + **`_dispatch_browser_action(page, cwd, params)`** (Lookup-Loop statt 5× `if/elif`; unbekanntes Action → gleicher `"Error: unknown action '…'"`-String wie vorher).
  - **`_run_browser(cwd, params)`** — Session-Lifecycle (launch → new_page → goto → optionales `wait_for_selector` → dispatch → close). **Semantik 1:1:** `launch()` AUFFERHALB des try/except (Fehler propagieren nach oben), Page-Fehler → `"Error: {e}"`-Strings (P1#3: breite `except` bewusst belassen).
- **Erste Testrunde (10 Browser-Tests rot):** Handler hatten initiale Signatur `(page, params)`, Dispatcher rief aber `(page, cwd, params)` → `TypeError`. Fix: alle 5 Handler auf `(page, cwd, params)` → danach grün. (Lektion: Signatur-Einheitlichkeit vor dem ersten Lauf prüfen — Dispatcher-Call ist die Quelle der Wahrheit.)
- **Verifikation:** A/B-Differential **16 Cases** gegen `git show HEAD:src/selma/tools.py` → `/tmp/old_tools.py` per **in-process Dual-Load** (**niemals** `git stash`, Stolperstein 25) → **16/16 byte-identisch** (alle 5 Actions, Default-Pfade, Fehler-Strings, launch-Fehler-Propagation, `wait_for`). **Suite 730 passed / 0 failed** (62 s), `tools.py` **99 %** (159 stmts / 2 Miss L343-344), Gesamt **99 %**. `ruff check` + `ruff format --check` grün (90 Dateien), `mypy src/selma` **0 errors / 30 files**, `find ~/.selma -type f` → 0, `/tmp/old_tools.py` isoliert geräumt (St-26/28).

### 12. ~~`memory_index.py:305 _hybrid_search(...)` — **83 Zeilen, 5-Param**~~ ✅ **erledigt 2026-09-22** (Commit `488a5e3`)
- **Smell:** Long function + 5 Parameter (inkl. `mtime_by_path`-Optional, das 2026-09-09 ein echtes Bug-Trigger hat).
- **Fix (umgesetzt):** `_hybrid_search` → **83 → 23 Z** (AST-verifiziert, 0 doppelte private Modul-Namen, 0 Inline-Blöcke > 10 Z; Body = reine Orchestrierung). Neue Symbole:
  - **`@dataclass _SearchContext`** (Modul-Ebene, L91) — 6 Felder (`query, fts_query, max_results, min_score, embedder, mtime_by_path`); ersetzt die 5-Param-Signatur, `None`-able Embedder ist jetzt ausdrücklich darstellbar.
  - **`_hybrid_fts_stage(ctx)`** (16 Z) — Stage 1: FTS5-Kandidaten-Pool, `sqlite3.OperationalError` → `[]`. **`_connect`-Call #1** (Reihenfolge unverändert — Stolperstein memory_index-Runde eingehalten).
  - **`_load_candidate_vectors(ctx, candidate_paths) -> dict[(path, chunk_idx), vec]`** (17 Z) — Stage 2: chunks_vec-Load, `Exception` → `{}` (BM25-degradation). **`_connect`-Call #2**.
  - **`_hybrid_rank(ctx, query_vec, fts_rows, stored)`** (37 Z) — Stage 3: Cosine+BM25-Mix, per-path-Counter für chunk_idx, `_apply_decay` mit `ctx.mtime_by_path`, `min_score`-Filter, Sort+Truncate.
  - **`search()`** baut den `ctx` (mtime_by_path via `_load_mtimes()` bei `temporal_decay`, sonst `{}`).
- **Defensive Erweiterung:** `ctx.embedder is None` → FTS-only-Fallback (neu; im Happy-Path unerreichbar via `search()`, da dort `self._embedder` geprüft wird — aber `_hybrid_search` ist semi-public API). eigener Test `test_hybrid_search_without_embedder_falls_back_to_fts`.
- **ACHTUNG (eingehalten):** `_connect`-Call-Reihenfolge in Tests unverändert (1. = FTS-Select, 2. = chunks_vec-Load) — `_flaky_connect`-Helper + 4 umbenannte 5-Param-Callstellen auf `_ctx(…)`.
- **Verifikation:** **53/53** `test_unit_memory.py` (52 + 1 neuer Test), **Full-Suite 731 passed / 0 failed** (62 s), `memory_index.py` **100 %** (249 stmts / 0 Miss), Gesamt **99 %**. `ruff check` + `ruff format --check` grün, `mypy src/selma` **0 errors / 30 files**, `find ~/.selma -type f` → 0. A/B-Differential **7/7 PASS** (full-hybrid / embed-None-Fallback / FTS-Stage-Fehler / flaky-chunks_vec / min_score 1.1 / min_score 0.0 / No-Result) + defensive-None-Case — in-process Dual-Load gegen `git show HEAD:src/selma/memory_index.py` → `/tmp/old_mi.py` (Stolperstein 25 — **nie** `git stash`); A/B-Bug in der ersten Skript-Runde: `@dataclass`-Modul fehlt in `sys.modules` → Fix: Moduls-Name eintragen vor `exec`. `_scratch_p212/` + `/tmp/old_mi.py` geräumt (St-26/28).

---

## P3 — Größere / strukturelle Refactors (mehrerige Runden)

### 13. `runtime.py` (1134 Zeilen, 99 % Coverage) — God-Module-Kandidat
- **Smell:** Datei ist das "Zentralwerk" mit 10+ Funktionen > 40 Zeilen (`agent_command`, `run_embedded_attempt`, `detect_attempt_error`, `execute_prompt`, …).
- **Fix (optional, erst wenn P1-P2 rum sind):** Aufspaltung in `runtime/session.py`, `runtime/attempt.py`, `runtime/error_detect.py`; `runtime.py` als Fassade/Re-Export.
- **Risk:** **hoch** — viele Import-Stellen, `selma.runtime` in sys.modules-Mocks (Stolperstein #6).
- **Nur mit Rolf-Grünlicht**, wenn nach P1-P2 das Gefühl besteht "es wird dicker".

### 14. ~~Data-Cluster: `session_key + session_id`-Paar wird durch 5+ Stellen weitergereicht~~ ✅ **erledigt 2026-09-23**
- **Smell:** Data clumps (siehe `agent_command`, `resolve_session`, `reset_session`, `agent_runtime._execute`).
- **Fix (umgesetzt):** `SessionRef(session_key, session_id)`-Frozen-Dataclass in `session_store.py` (modul-Level); `resolve_session(store, ref, config)` + `runtime.get_session(ref, config, cwd)` nehmen jetzt ein `SessionRef` statt zwei optionaler Parallel-Parameter. `CommandContext.session_ref: SessionRef` (Pydantic-Feld, no-default-Gruppe). **Öffentliche API stabil:** `run_agent`/`agent_command` behalten `session_key=`/`session_id=`-KWArgs; `_prepare_command` baut das Ref. `ctx.session_key`-Reader in command_manager/agent_runtime/gateway sind `NormalizedTurnInput`-Objekte → nicht betroffen.
- **Abnahme (2026-09-23):** Suite **731 passed**, ruff + `ruff format` grün, `mypy src/selma` 0/30, Gesamt-Coverage **99 %** (`session_store.py` 98 % → Ziel ≥ zuvor), `find ~/.selma -type f` → 0, A/B-Dual-Load (git `HEAD`: vs. Worktree, St-25 **kein Stash**, St-30 `sys.modules` vor `exec`) → 4/4 PASS (key/id/Validierung, public-API-Pfad).

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
