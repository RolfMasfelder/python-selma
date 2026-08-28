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
- [x] task_manager.py — 2188 B — 0% → 100% (2026-08-27, tests/unit/test_unit_task_manager.py)
- [x] tracing.py — 2868 B — 73% → 88% (2026-08-27, tests/unit/test_unit_tracing.py)
- [x] adapter_telegram.py — 2884 B — 0% → 100% (2026-08-27, tests/unit/test_unit_adapter_telegram.py)
- [ ] skills.py — 3556 B — 0%
- [ ] compaction.py — 5148 B — 0%
- [ ] my_system_prompt.py — 6560 B — 35%
- [ ] setup.py — 6982 B — 0%
- [ ] dashboard.py — 7067 B — 0%
- [ ] gateway.py — 8754 B — 0%
- [ ] config.py — 9521 B — 62%
- [ ] heartbeat.py — 9764 B — 69%
- [ ] agent_runtime.py — 13289 B — 0%
- [ ] agent.py — 15286 B — 38%
- [ ] tools.py — 16238 B — 42%
- [ ] command_manager.py — 17211 B — 0%
- [ ] session_store.py — 19219 B — 0%
- [ ] memory_index.py — 20531 B — 60%
- [ ] agent_session.py — 27266 B — 0%
- [ ] my_tools.py — 30770 B — 8%
- [ ] system_prompt.py — 32269 B — 0%
- [ ] runtime.py — 38113 B — 0%

Nicht auf der Liste (bereits ≥ 80%): resource_loader.py (91%), __init__.py (100%)
