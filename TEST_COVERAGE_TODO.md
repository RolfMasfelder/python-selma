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
- [x] task_manager.py — 2188 B — 0% → 91% (2026-08-27, tests/unit/test_unit_task_manager.py)
- [x] tracing.py — 2868 B — 73% → 88% (2026-08-27, tests/unit/test_unit_tracing.py)
- [x] adapter_telegram.py — 2884 B — 0% → 100% (2026-08-27, tests/unit/test_unit_adapter_telegram.py)
- [x] skills.py — 3556 B — 0% → 100% (2026-08-28, tests/unit/test_unit_skills.py)
- [x] compaction.py — 5148 B — 39% → 100% (2026-08-28, tests/unit/test_unit_compaction.py)
- [x] my_system_prompt.py — 6560 B — 35% → 100% (2026-08-28, tests/unit/test_unit_my_system_prompt.py)
- [x] setup.py — 6982 B — 0% → 94% (2026-08-28, tests/unit/test_unit_setup.py)
- [x] dashboard.py — 7067 B — 0% → 95% (2026-08-29, tests/unit/test_unit_dashboard.py)
- [x] gateway.py — 8754 B — 0% → 97% (2026-09-01, tests/unit/test_unit_gateway.py)
- [x] config.py — 9521 B — 62% → 99% (2026-09-01, tests/unit/test_unit_config.py)
- [x] heartbeat.py — 9764 B — 69% → 100% (2026-09-06, tests/unit/test_unit_heartbeat.py)
- [x] agent_runtime.py — 13289 B — 0% → 100% (2026-09-03, tests/unit/test_unit_agent_runtime.py)
- [x] agent.py — 15286 B — 38% → 100% (2026-09-06, tests/unit/test_unit_agent.py)
- [x] tools.py — 16238 B — 42% → 99% (2026-09-08, tests/unit/test_unit_tools.py)
- [x] command_manager.py — 17211 B — 16% → 99% (2026-09-07, tests/unit/test_unit_command_manager.py)
- [x] session_store.py — 19219 B — 27% → 97% (2026-09-06, tests/unit/test_unit_session_store.py)
- [ ] memory_index.py — 20531 B — 60% — 🎯 **NÄCHSTER HEBEL**
- [ ] agent_session.py — 27266 B — 0%
- [x] my_tools.py — 30770 B — 14% → 91% (2026-09-08, tests/unit/test_unit_my_tools.py)
- [ ] system_prompt.py — 32269 B — 0%
- [x] runtime.py — 38113 B — 34% → 99% (2026-09-10, tests/unit/test_unit_runtime.py, 84 Tests)

Nicht auf der Liste (bereits ≥ 80%): resource_loader.py (91%), __init__.py (100%)
