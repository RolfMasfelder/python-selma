# Testabdeckung TODO-Liste (< 80%)

Stand: 2026-08-27, aus `coverage report` + Dateigröße (Bytes) aufsteigend sortiert.
Ziel: Jede Datei in `src/selma/` auf ≥ 80% Abdeckung (Statements).
Start: kleinste Datei zuerst.

- [x] test_helper.py — 595 B — 0% → 100% (2026-08-27, tests/unit/test_unit_test_helper.py)
- [x] channel_adapter.py — 836 B — 0% → 100% (2026-08-27)
- [x] data.py — 992 B — 0% → 100% (2026-08-27, tests/unit/test_unit_channel_adapter_data.py)
- [x] helper.py — 1017 B — 44% → 100% (2026-08-27, tests/unit/test_unit_helper.py)
- [x] my_resource_loader.py — 1128 B — 0%/50% → 100% (2026-08-27, tests/unit/test_unit_my_resource_loader.py)
- [x] adapter_webchat.py — 1713 B — 0% → 100% (2026-08-27, tests/unit/test_unit_adapter_webchat.py)
- [x] delivery.py — 1754 B — 0%/41% → 100% (2026-08-27, tests/unit/test_unit_delivery.py)
- [x] task_manager.py — 2188 B — 0% → 91% (2026-08-27, tests/unit/test_unit_task_manager.py; 2026-09-01: Cross-Loop-Fix in shutdown() + tests/integration/test_integration_task_manager.py, 7 Tests)
- [x] tracing.py — 2868 B — 73% → 88% (2026-08-27, tests/unit/test_unit_tracing.py)
- [x] adapter_telegram.py — 2884 B — 0% → 100% (2026-08-27, tests/unit/test_unit_adapter_telegram.py)
- [x] skills.py — 3556 B — 0% → 100% (2026-08-28, tests/unit/test_unit_skills.py; dabei Test-Fix: erwartete CamelCase-Namen, Code nutzt Frontmatter-Name)
- [x] compaction.py — 5148 B — 39% → 100% (2026-08-28, tests/unit/test_unit_compaction.py — 5 Tests: fehlende Datei, create_Fehler, zu wenig Messages, compact_Fehler, Erfolg inkl. Token-Zählung)
- [x] my_system_prompt.py — 6560 B — 35% → 100% (2026-08-28, tests/unit/test_unit_my_system_prompt.py — 10 Tests: Defaults/None, Custom Prompt, Tool-Liste/Merge, alle Guideline-Äste, Guideline-Dedupe, Kontext-Sektion, Helfer)
- [x] setup.py — 6982 B — 0% → 94% (2026-08-28, tests/unit/test_unit_setup.py — 11 Tests: Config-Struktur, setup fresh/idempotent/fehler, templates 4 Fälle, skills sync/stale/noop; ungedeckt: unerreichbarer 'all-skipped'-Zweig (SKILL.md wird per Definition immer kopiert) + __main__)
- [x] dashboard.py — 7067 B — 0% → 95% (2026-08-29, tests/unit/test_unit_dashboard.py — 12 Tests: parse_sse_events, read/write_raw_file, App-Rendering Initial/Chat-Erfolg/ConnectError/SSE-Error/Settings-Dialog (Edit/Save/Invalid/Discard)); ungedeckt: unerreichbare Exception-Fall-Branch (json.JSONDecodeError im try/except in settings_dialog wird nie getestet, weil die App vorher bricht)
- [x] gateway.py — 8754 B — 0% → 97% (2026-09-01: Unit-Tests bestanden; Cross-Loop-Kontamination fixiert, raising-wait_for-Test-Fake korrigiert (ensure_future statt cancel-auf-Coroutine); ungedeckt: 53, 248-251)
- [x] config.py — 9521 B — 62% → 99% (2026-09-01, tests/unit/test_unit_config.py — 22 Tests: Model-Defaults, toolsAllow-Validator, is_channel_enabled, TELEGRAM_TOKEN-Env, load_config (Parsing/fehlt/invalid-JSON/RuntimeError/Cache-Hit/Cache-Expiry), get_default_model, resolve_timeout/tools_allow/thinking_default)
- [x] heartbeat.py — 9764 B — 69% → 100%
- [x] agent_runtime.py — 13289 B — 0% → 100% (2026-09-03, tests/unit/test_unit_agent_runtime.py — 32 Tests: RunResult/RunParams-Defaults + Serialization, RunLaneManager (Lock/Active-Done/Cross-Key), SessionFactory (Cache-Hit/Invalidate/Idempotent), SystemPromptBuilder (light HEARTBEAT.md + Safety + Runtime, full ResourceLoader + Truncation 20k), EventSubscriber (message_update/message_end/tool_*/agent_end), RunOrchestrator (ok/timeout/error/Cross-Lane/Sequential); Stolpersteine: message_end muss WÄHREND prompt() gesendet werden (Orchestrator liest final_reply nach prompt-Return), build_system_prompt() wird positionell mit BuildSystemPromptOptions aufgerufen (call_args.args[0]), timeout_ms über **kw, nicht als Named-Param im Helper)
- [x] agent.py — 15286 B — 38% → 100% (2026-09-06, tests/unit/test_unit_agent.py — 15 Tests: Data-Classes/Defaults, prompt()-Double-Run, subscribe/unsubscribe, Plain-Text-Stream Event-Reihenfolge, Tool-Call-Delta-Akkumulation (id/name/arguments über 2 Chunks), sync+async Tool-Exec, unknown tool, Tool-Exception, invalid-JSON-Fallback, thinking_level→reasoning_effort, convert_to_llm-Hook, LLM-Fehler + agent_end, Subscriber-Exception-Isolation; Stolperstein: Tool-Call-Argumente über Chunks splitten NUR an JSON-gültigen Grenzen — Split mitten im Key (z. B. '{"va"' + 'ue": "x"}') ergibt konkateniert invalides JSON → {}-Fallback → Tool-Fehler; Fake via SimpleNamespace-Chunks, streamender fake_create als async-Generator)
- [ ] tools.py — 16238 B — 42%
- [x] command_manager.py — 17211 B — 16% → 99% (2026-09-07, tests/unit/test_unit_command_manager.py — 45 Tests; 1 Miss: _memory_flush-Wicklung nur via /compact, im Test gepatcht)
- [x] session_store.py — 19219 B — 27% → 97% (2026-09-06, tests/unit/test_unit_session_store.py)
- [ ] memory_index.py — 20531 B — 60%
- [ ] agent_session.py — 27266 B — 0%
- [x] my_tools.py — 30770 B — 14% → 91% (2026-09-08, tests/unit/test_unit_my_tools.py — 64 Tests; Stolpersteine: rg IST installiert → `no_rg`-Fixture (subprocess.run→FileNotFoundError) für python-Branch, TimeoutExpired.stdout=None, ENAMETOOLONG bei 60k-Char-Namen (350×151B statt), read_text() normalisiert CRLF → CRLF-Restore toter Code)
- [ ] system_prompt.py — 32269 B — 0%
- [ ] runtime.py — 38113 B — 0%

Nicht auf der Liste (bereits ≥ 80%): resource_loader.py (91%), __init__.py (100%)
