# ============================================================
# command_manager.py unit tests
#
# Deckung für selma/command_manager.py (CommandManager):
#   - handle(): Dispatch (alle Befehle, Alias, Case-Insensitiv,
#     Whitespace-Trim, "Not a command", Unknown)
#   - /model: get (+ Default-Fallback) & set (mit/ohne Session)
#   - /models: Liste, leer, Fehler (list_ollama_models-Mock)
#   - /think: get (Default off), set, Invalid, Alias, case-insens.
#   - /reset & /new: mit Session (+ Reset), ohne Session
#   - /compact: ohne Session, ok, compaction failed, nothing to do,
#     tool-callback-Emission via DeliveryContext
#   - /status: ohne/mit Session (inkl. Message-Zeilen, Heartbeat-Fall)
#   - /allowlist: "all" & Liste & Disabled
#   - /tools: alle erlaubt & eingeschränkt (Disabled-Liste)
#   - /skills: none & Skill mit Frontmatter
#   - /help: ohne/mit Skills
#   - /config: Usage, nicht gefunden, show (JSON-Breakdown)
#   - /commands: Katalog
#
# Konventionen (siehe tests/unit/test_unit_session_store.py):
#   - KEIN pytest-asyncio → async-Aufrufe via run(coro)-Helper
#     (frische asyncio.new_event_loop()).
#   - Isolation: SELMA_STATE_DIR-Env + <root>/.selma (autouse-Fix)
#     → kein ~/.selma-Fallback.
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import asyncio
import json
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import selma.command_manager as cm
from selma.command_manager import CommandManager
from selma.compaction import CompactionResult
from selma.config import ActiveHoursConfig, SelmaConfig
from selma.data import NormalizedTurnInput
from selma.runtime import DeliveryContext
from selma.session_store import SessionRecord, SessionStore, _store_path


# ── Isolation (function-scope autouse) ────────────────────────
@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Dokumentiert in MEMORY.md: SELMA_STATE_DIR-Env + <root>/.selma doppelt absichern."""
    monkeypatch.setenv("SELMA_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / ".selma").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


def _h(
    mgr: CommandManager, body: str | None = "/status", session_key: str | None = "agent:main:test", **kw: Any
) -> str:
    """handle() ist async → immer über _run() aufrufen."""
    ctx = _ctx(body, session_key)
    return _run(mgr.handle(ctx, **kw))


def _ctx(body: str | None, session_key: str | None) -> NormalizedTurnInput:
    return NormalizedTurnInput(body=body, body_for_commands=body, session_key=session_key)


def _config(
    *,
    model: str = "ollama/llama3.2",
    tools_allow: str | list[str] = "all",
    thinking: str = "low",
) -> SelmaConfig:
    cfg = SelmaConfig()
    cfg.model.model = model
    cfg.model.thinking = thinking
    cfg.agent.toolsAllow = tools_allow
    return cfg


def _mgr(tmp_path: Path, **cfg_kwargs: Any) -> CommandManager:
    return CommandManager(config=_config(**cfg_kwargs), cwd=str(tmp_path))


def _seed_session(
    tmp_path: Path,
    key: str = "agent:main:test",
    *,
    model: str | None = None,
    thinking: str | None = None,
    provider: str | None = None,
    last: str | None = None,
    transcript_file: str | None = None,
    session_id: str = "s1d2f3a4",
) -> SessionRecord:
    """Eine in-memory Session im tmp-Store, damit _get_session_record sie findet."""
    rec = SessionRecord(
        session_key=key,
        session_id=session_id,
        model_override=model,
        provider_override=provider,
        thinking_level=thinking,
        last_interaction_at=last,
        transcript_file=transcript_file,
    )
    store = SessionStore(sessions={key.lower(): rec}, store_path=str(_store_path(agent_id="main", cwd=str(tmp_path))))
    cm.save_session_store(store)
    return rec


def _patch_skills(monkeypatch: pytest.MonkeyPatch, name: str | None = None, desc: str = "") -> None:
    """find_skill_files fälschen; read_text liefert echtes Frontmatter-Format."""
    if name is None:
        monkeypatch.setattr(cm, "find_skill_files", lambda cwd: [])
        return

    class _FakeSkillPath:
        parent = SimpleNamespace(name="dircase")

        def read_text(self, encoding: str = "utf-8") -> str:
            assert encoding == "utf-8"
            return f'---\nname: "{name}"\ndescription: {desc}\n---\nbody\n'

    monkeypatch.setattr(cm, "find_skill_files", lambda cwd: [_FakeSkillPath()])


# ══════════════════════════════════════════════════════════════
# handle() Dispatch
# ══════════════════════════════════════════════════════════════


def test_handle_rejects_non_command(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    assert _h(mgr, "hello") == "Not a command."
    assert _h(mgr, "/") == "Unknown command: `/`."
    assert _h(mgr, None) == "Not a command."
    assert _h(mgr, "   ") == "Not a command."


def test_handle_dispatches_all_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # list_ollama_models deterministisch fälschen (sonst echter Ollama-HTTP-Call)
    async def _fake_models(base_url: str = "x") -> list[SimpleNamespace]:
        return [SimpleNamespace(name="m1")]

    import selma.agent_session as _as

    monkeypatch.setattr(_as, "list_ollama_models", _fake_models)

    # /compact-Pfad: echte memory_flush/compact_session (LLM) verhindern
    async def _fake_flush(session_key: str, cwd: str) -> None:
        return None

    async def _fake_compact(session_file: str, config: SelmaConfig) -> CompactionResult:
        return CompactionResult(ok=False, compacted=False, reason="stub")

    monkeypatch.setattr(cm, "_memory_flush_fn", _fake_flush)
    monkeypatch.setattr(cm, "compact_session", _fake_compact)

    mgr = _mgr(tmp_path)
    _seed_session(tmp_path)
    # Nur "soll funktionieren": jeder Command antwortet weder mit
    # "Not a command" noch mit "Unknown command"
    bodies = [
        "/model",
        "/models",
        "/think off",
        "/think low",
        "/thinking medium",
        "/t high",
        "/config show",
        "/reset",
        "/new",
        "/help",
        "/tools",
        "/allowlist",
        "/status",
        "/commands",
        "/compact",
        "/skills",
    ]
    for b in bodies:
        outcome = _h(mgr, b)
        assert not outcome.startswith("Not a command"), f"dispatch failed on {b}: {outcome}"
        assert not outcome.startswith("Unknown command"), f"unknown cmd on {b}: {outcome}"


def test_handle_unknown_command(tmp_path: Path) -> None:
    assert _h(_mgr(tmp_path), "/wat") == "Unknown command: `/wat`."


def test_handle_case_insensitive(tmp_path: Path) -> None:
    assert _h(_mgr(tmp_path), "/Model") == "Current model: `llama3.2`"


def test_handle_trims_whitespace(tmp_path: Path) -> None:
    assert _h(_mgr(tmp_path), "   /model  ") == "Current model: `llama3.2`"


# ══════════════════════════════════════════════════════════════
# /model
# ══════════════════════════════════════════════════════════════


def test_model_get_no_override_uses_default(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path, model="ollama/qwen3")
    assert _h(mgr, "/model") == "Current model: `qwen3`"


def test_model_get_with_override(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path, model="ollama/qwen3")
    _seed_session(tmp_path, model="gpt4")
    assert _h(mgr, "/model") == "Current model: `gpt4`"


def test_model_set_persists(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    _seed_session(tmp_path)
    assert _h(mgr, "/model gpt4") == "Model set to `gpt4`."
    # Und der Store wurde tatsächlich aktualisiert
    store = cm.load_session_store(cwd=str(tmp_path))
    assert store.sessions["agent:main:test"].model_override == "gpt4"


def test_model_set_without_session_still_reports(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    # Keine Session im Store — "Model set to." wird trotzdem zurückgegeben
    out = _h(mgr, "/model gpt4", session_key="agent:main:ghost-test-key")
    assert out == "Model set to `gpt4`."


# ══════════════════════════════════════════════════════════════
# /models (list_ollama_models-Mock)
# ══════════════════════════════════════════════════════════════


def test_models_lists_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(base_url: str = "x") -> list[SimpleNamespace]:
        return [SimpleNamespace(name="gpt4"), SimpleNamespace(name="qwen3")]

    import selma.agent_session as _as

    monkeypatch.setattr(_as, "list_ollama_models", _fake)
    out = _h(_mgr(tmp_path), "/models")
    assert "Available models:" in out
    assert "`gpt4`" in out and "`qwen3`" in out


def test_models_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(base_url: str = "x") -> list[SimpleNamespace]:
        return []

    import selma.agent_session as _as

    monkeypatch.setattr(_as, "list_ollama_models", _fake)
    assert _h(_mgr(tmp_path), "/models") == "No models available."


def test_models_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake(base_url: str = "x") -> list[SimpleNamespace]:
        raise RuntimeError("boom")

    import selma.agent_session as _as

    monkeypatch.setattr(_as, "list_ollama_models", _fake)
    out = _h(_mgr(tmp_path), "/models")
    assert "Could not fetch models:" in out


# ══════════════════════════════════════════════════════════════
# /think (+ alias /thinking, /t)
# ══════════════════════════════════════════════════════════════


def test_think_get_defaults_to_off(tmp_path: Path) -> None:
    assert _h(_mgr(tmp_path), "/think") == "Current thinking level: `off`"


def test_think_get_with_override(tmp_path: Path) -> None:
    _seed_session(tmp_path, thinking="high")
    assert _h(_mgr(tmp_path), "/think") == "Current thinking level: `high`"


def test_think_set_persists(tmp_path: Path) -> None:
    _seed_session(tmp_path)
    assert _h(_mgr(tmp_path), "/think high") == "Thinking level set to `high`."
    store = cm.load_session_store(cwd=str(tmp_path))
    assert store.sessions["agent:main:test"].thinking_level == "high"


def test_think_off_maps_to_none(tmp_path: Path) -> None:
    _seed_session(tmp_path, thinking="medium")
    assert _h(_mgr(tmp_path), "/think off") == "Thinking level set to `off`."
    store = cm.load_session_store(cwd=str(tmp_path))
    assert store.sessions["agent:main:test"].thinking_level is None


def test_think_invalid_level_rejected(tmp_path: Path) -> None:
    _seed_session(tmp_path)
    assert _h(_mgr(tmp_path), "/think super") == "Invalid level `super`. Use: off, low, medium, high."
    store = cm.load_session_store(cwd=str(tmp_path))
    assert store.sessions["agent:main:test"].thinking_level is None


def test_think_set_case_insensitive(tmp_path: Path) -> None:
    _seed_session(tmp_path)
    assert _h(_mgr(tmp_path), "/think HIGH") == "Thinking level set to `high`."
    store = cm.load_session_store(cwd=str(tmp_path))
    assert store.sessions["agent:main:test"].thinking_level == "high"


def test_thinking_aliases(tmp_path: Path) -> None:
    _seed_session(tmp_path)
    assert _h(_mgr(tmp_path), "/thinking medium") == "Thinking level set to `medium`."
    assert _h(_mgr(tmp_path), "/t low") == "Thinking level set to `low`."


# ══════════════════════════════════════════════════════════════
# /reset & /new
# ══════════════════════════════════════════════════════════════


def test_reset_rotates_session_id(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    rec = _seed_session(tmp_path, session_id="old-one", thinking="high")
    out = _h(mgr, "/reset")
    assert out == "Session reset. Starting fresh."
    store = cm.load_session_store(cwd=str(tmp_path))
    new_rec = store.sessions["agent:main:test"]
    assert new_rec.session_id != "old-one"
    assert new_rec.thinking_level == rec.thinking_level  # bleibt erhalten (reset_session)
    assert Path(store.store_path).exists()


def test_reset_without_session_reports(tmp_path: Path) -> None:
    assert _h(_mgr(tmp_path), "/reset", session_key="agent:main:nobody") == "No active session found."


def test_new_is_alias_for_reset(tmp_path: Path) -> None:
    _seed_session(tmp_path, session_id="before")
    _h(_mgr(tmp_path), "/new")
    store = cm.load_session_store(cwd=str(tmp_path))
    assert store.sessions["agent:main:test"].session_id != "before"


# ══════════════════════════════════════════════════════════════
# /compact
# ══════════════════════════════════════════════════════════════


def test_compact_no_session(tmp_path: Path) -> None:
    assert (
        _h(_mgr(tmp_path), "/compact", session_key="agent:main:nobody")
        == "No active session found — nothing to compact."
    )


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (dict(ok=False, compacted=False, reason="llm-fail"), "Compaction failed: llm-fail"),
        (dict(ok=True, compacted=False, reason="session too short"), "Nothing compacted: session too short"),
        (
            dict(ok=True, compacted=True, reason="", tokens_before=10000, tokens_after=2500),
            "Session compacted. Tokens: ~10000 → ~2500 (−7500, −75%)",
        ),
        (
            dict(ok=True, compacted=True, reason="", tokens_before=0, tokens_after=0),
            "Session compacted. Tokens: ~0 → ~0 (−0, −0%)",  # Divisionsschutz
        ),
    ],
)
def test_compact_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: dict, expected: str) -> None:
    mgr = _mgr(tmp_path)
    rec = _seed_session(tmp_path)
    transcript = tmp_path / "s1.jsonl"
    transcript.write_text('{"type":"m"}\n{"type":"m"}\n', encoding="utf-8")
    rec.transcript_file = str(transcript)
    cm.save_session_store(cm.load_session_store(cwd=str(tmp_path)))

    calls: list[str] = []

    async def _fake_compact(session_file: str, config: SelmaConfig) -> CompactionResult:
        calls.append("compact")
        return CompactionResult(**result)

    async def _fake_flush(session_key: str, cwd: str) -> None:
        calls.append(f"flush:{session_key}")

    monkeypatch.setattr(cm, "compact_session", _fake_compact)
    monkeypatch.setattr(cm, "_memory_flush_fn", _fake_flush)

    out = _h(mgr, "/compact")
    assert out == expected
    # Beide Seiten in der richtigen Reihenfolge aufgerufen
    assert calls == ["flush:agent:main:test", "compact"]


def test_compact_emits_tool_callbacks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _mgr(tmp_path)
    rec = _seed_session(tmp_path)
    transcript = tmp_path / "s1.jsonl"
    transcript.write_text('{"type":"m"}\n', encoding="utf-8")
    rec.transcript_file = str(transcript)
    cm.save_session_store(cm.load_session_store(cwd=str(tmp_path)))

    emitted: list[tuple[str, dict[str, Any]]] = []
    delivery = DeliveryContext(on_tool_call=lambda name, args: emitted.append((name, args)))

    async def _fake_compact(session_file: str, config: SelmaConfig) -> CompactionResult:
        return CompactionResult(ok=True, compacted=True, tokens_before=100, tokens_after=50)

    async def _fake_flush(session_key: str, cwd: str) -> None:
        return None

    monkeypatch.setattr(cm, "compact_session", _fake_compact)
    monkeypatch.setattr(cm, "_memory_flush_fn", _fake_flush)

    out = _h(mgr, "/compact", delivery=delivery)
    assert "Session compacted." in out
    names = [name for name, _ in emitted]
    assert "🔄 Saving memory" in names and "🗜️ Compacting history" in names


# ══════════════════════════════════════════════════════════════
# /status
# ══════════════════════════════════════════════════════════════


def test_status_no_session(tmp_path: Path) -> None:
    out = _h(_mgr(tmp_path), "/status", session_key="agent:main:nobody")
    assert "**Selma Status**" in out
    assert "`provider`   ollama" in out
    assert "`model`      llama3.2" in out
    # ollama → resolve_thinking_default liefert None → "off"-Fallback
    assert "`thinking`   off" in out
    assert "`session`    agent:main:nobody" in out
    assert "`session_id` —" in out
    assert "`messages`   —" in out
    assert "`last`       —" in out


def test_status_with_session_and_transcript(tmp_path: Path) -> None:
    transcript = tmp_path / "s.jsonl"
    transcript.write_text('{"type":"a"}\n{"type":"b"}\n{"type":"c"}\n', encoding="utf-8")
    _seed_session(
        tmp_path,
        session_id="abc123",
        provider="anthropic",
        model="claude-3",
        thinking="high",
        last="2026-09-06T10:00:00Z",
        transcript_file=str(transcript),
    )
    out = _h(_mgr(tmp_path), "/status")
    assert "`provider`   anthropic" in out
    assert "`model`      claude-3" in out
    assert "`thinking`   high" in out
    assert "`messages`   3" in out
    assert "`session_id` abc123" in out
    assert "2026-09-06T10:00:00" in out  # last


def test_status_heartbeat_active_hours_flags(tmp_path: Path) -> None:
    mgr = _mgr(tmp_path)
    cfg = mgr._config
    cfg.heartbeat.every = "30m"
    cfg.heartbeat.target = "last"
    cfg.heartbeat.light_context = True
    cfg.heartbeat.isolated_session = True
    cfg.heartbeat.active_hours = ActiveHoursConfig(start="09:00", end="17:00")
    out = _h(mgr, "/status", session_key="agent:main:x")
    assert "`heartbeat`  30m" in out
    assert "`hb.target`  last" in out
    assert "(light, isolated, active 09:00–17:00)" in out


def test_status_heartbeat_disabled_shows_off(tmp_path: Path) -> None:
    out = _h(_mgr(tmp_path), "/status", session_key="agent:main:x")
    assert "`heartbeat`  off" in out
    assert "`hb.target`  —" in out
    assert "`hb.next`    —" in out


def test_status_next_heartbeat_shown_when_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mgr = _mgr(tmp_path)
    mgr._config.heartbeat.every = "1h"
    from datetime import datetime

    import selma.heartbeat as hb

    monkeypatch.setattr(hb, "next_heartbeat_at", datetime.now(UTC).replace(microsecond=0))
    out = _h(mgr, "/status", session_key="agent:main:x")
    # next_heartbeat_at gesetzt + every != off → Zeile zeigt eine Uhrzeit, kein "—"
    assert "`hb.next`    " in out and "`hb.next`    —" not in out


# ══════════════════════════════════════════════════════════════
# /allowlist, /tools
# ══════════════════════════════════════════════════════════════


def test_allowlist_all(tmp_path: Path) -> None:
    out = _h(_mgr(tmp_path, tools_allow="all"), "/allowlist")
    assert "`all` (no restrictions)" in out
    assert "Set `agent.toolsAllow`" in out


def test_allowlist_subset(tmp_path: Path) -> None:
    out = _h(_mgr(tmp_path, tools_allow=["read", "write"]), "/allowlist")
    assert "**Tool allowlist** (2 tool(s))" in out
    assert "`read`" in out and "`write`" in out
    # Nicht-erlaubte werden als disabled gelistet
    assert "Disabled:" in out
    assert "`exec`" in out and "`browser`" in out


def test_tools_all(tmp_path: Path) -> None:
    from selma.tools import ALL_TOOL_NAMES, get_tool_descriptions

    expected = len(ALL_TOOL_NAMES)
    out = _h(_mgr(tmp_path, tools_allow="all"), "/tools")
    assert f"**Active tools** ({expected}/{expected})" in out
    for name in ALL_TOOL_NAMES:
        assert f"`{name}`" in out
    # Beschreibungen werden ausgegeben (falls vorhanden)
    for _name, desc in get_tool_descriptions().items():
        if desc:
            assert desc in out


def test_tools_restricted(tmp_path: Path) -> None:
    from selma.tools import ALL_TOOL_NAMES

    out = _h(_mgr(tmp_path, tools_allow=["read", "grep"]), "/tools")
    assert f"**Active tools** (2/{len(ALL_TOOL_NAMES)})" in out
    assert "`read`" in out and "`grep`" in out
    active_section = out.split("Disabled:")[0]
    assert "`browser`" not in active_section  # nur in Disabled-Sektion
    assert "Disabled:" in out


# ══════════════════════════════════════════════════════════════
# /skills, /help
# ══════════════════════════════════════════════════════════════


def test_skills_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_skills(monkeypatch, "Foo", "d1")
    out = _h(_mgr(tmp_path), "/skills")
    assert "**Skills** (1)" in out
    assert "`Foo` — d1" in out


def test_skills_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_skills(monkeypatch)
    assert _h(_mgr(tmp_path), "/skills") == "No skills found."


def test_help_without_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_skills(monkeypatch)
    out = _h(_mgr(tmp_path), "/help")
    assert "**Commands**" in out
    assert "`/model [name]`" in out
    assert "`/healthcheck`" in out


def test_help_with_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_skills(monkeypatch, "Bar", "bar-desc")
    out = _h(_mgr(tmp_path), "/help")
    assert "**Skills**" in out
    assert "`Bar`" in out
    assert "bar-desc" in out


# ══════════════════════════════════════════════════════════════
# /config show
# ══════════════════════════════════════════════════════════════


def test_config_bad_arg(tmp_path: Path) -> None:
    assert _h(_mgr(tmp_path), "/config") == "Usage: `/config show`"
    assert _h(_mgr(tmp_path), "/config hide") == "Usage: `/config show`"


def test_config_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # _cmd_config nutzt RELATIVEN Pfad (".selma/selma.json") → CWD bestimmen.
    # Chdir auf tmp_path: dort existiert .selma/, aber KEIN selma.json.
    monkeypatch.chdir(tmp_path)
    assert _h(_mgr(tmp_path), "/config show") == "Config file not found."


def test_config_show_pretty_prints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # _cmd_config nutzt Path(".selma/selma.json") relativ zum Prozess-CWD → chdir'n.
    payload = {"top": {"a": 1}, "list": [1, 2]}
    target = tmp_path / "project"
    (target / ".selma").mkdir(parents=True)
    (target / ".selma" / "selma.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.chdir(target)
    out = _h(_mgr(tmp_path), "/config show")
    assert out.startswith("```json")
    assert json.loads(out[len("```json") :].removesuffix("```")) == payload


# ══════════════════════════════════════════════════════════════
# /commands
# ══════════════════════════════════════════════════════════════


def test_commands_catalogue(tmp_path: Path) -> None:
    out = _h(_mgr(tmp_path), "/commands")
    assert "**Available commands**" in out
    for expected in [
        "`/model [name]`",
        "`/models`",
        "`/think <level>`",
        "`/reset`",
        "`/new`",
        "`/compact`",
        "`/config show`",
        "`/allowlist`",
        "`/status`",
        "`/tools`",
        "`/commands`",
        "`/skills`",
        "`/help`",
        "`/healthcheck`",
        "`/skill <name> [input]`",
    ]:
        assert expected in out
