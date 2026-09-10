# ============================================================
# test_unit_runtime.py
# Deckt src/selma/runtime.py ab:
#   Layer 1:  _BlockChunker, Lifecycle-Events, _resolve_skills_snapshot,
#             get_session, detect_bootstrap_mode, agent_command
#   Layer 2:  pick_fallback_thinking_level, handle_aborted,
#             repair_thinking_not_supported, repair_context_overflow,
#             _error_result, run_embedded_pi_agent, memory_flush
#   Layer 3:  detect_attempt_error, subscribe_output_collector,
#             execute_prompt, build_attempt_result, run_embedded_attempt
# Stil: sync-Tests + run(coro)-Helper (frische Event-Loops),
# Fakes via monkeypatch auf runtimes Modulattribute (kein pytest-asyncio).
# ============================================================

from __future__ import annotations

import asyncio
import types
from typing import Any

import pytest

from selma import runtime as rt
from selma.agent import AgentEvent, AgentTool, ToolSchema
from selma.config import SelmaConfig
from selma.session_store import SessionRecord, SessionStore, SkillsSnapshot


def run[T](coro: Any) -> T:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture(autouse=True)
def isolate_state_dir(
    tmp_path,
    monkeypatch,
):
    """
    Tests in runtime.py dürfen NICHT in ~/.selma oder <projekt>/.selma schreiben.
    Prio-1 (SELMA_STATE_DIR) + Prio-2-Ordner anlegen — doppelte Absicherung.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("SELMA_STATE_DIR", str(state_dir))
    (tmp_path / ".selma").mkdir()
    yield


@pytest.fixture(autouse=True)
def clean_lifecycle_listeners():
    """_LIFECYCLE_LISTENERS ist modul-global — Test-Listener nicht übertreten."""
    saved = set(rt._LIFECYCLE_LISTENERS)
    rt._LIFECYCLE_LISTENERS.clear()
    try:
        yield
    finally:
        rt._LIFECYCLE_LISTENERS.clear()
        rt._LIFECYCLE_LISTENERS.update(saved)


def make_tool(name: str) -> AgentTool:
    return AgentTool(
        name=name,
        description=f"fake {name}",
        parameters=ToolSchema(type="object", properties={}),
        execute=lambda *a, **kw: None,
    )


# ── FakeSession: AgentSession-Interface für runtime-Tests ─────
#
# Benötigte Oberfläche (duck-typing):
#   .subscribe(listener) -> callable      (unsubscribe)
#   .prompt(text) -> Coroutine             (erlöst Event(s) + liefert)
#   .state.messages -> list
#   .session_id -> str | None
#   .unsubscribe()                        (eigentlich via subscribe-Return)


class FakeSession:
    def __init__(
        self,
        message: str | None = "Hallo Welt",
        timeout_seconds: float | None = None,
        raise_exc: Exception | None = None,
        session_id: str | None = "sess-1",
        message_count: int = 2,
    ) -> None:
        self._message = message
        self._timeout_seconds = timeout_seconds
        self._raise_exc = raise_exc
        self._session_id = session_id
        self.state = types.SimpleNamespace(messages=list(range(message_count)))
        self.subscribers: list[Any] = []
        self.prompt_calls: list[str] = []

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def subscribe(self, listener: Any) -> Any:
        self.subscribers.append(listener)
        return lambda: self.subscribers.remove(listener)

    async def prompt(self, text: str) -> None:
        self.prompt_calls.append(text)
        if self._timeout_seconds is not None:
            await asyncio.sleep(self._timeout_seconds)
        if self._message is not None:
            for sub in list(self.subscribers):
                sub(AgentEvent(type="message_update", payload=self._message))
        for sub in list(self.subscribers):
            sub(AgentEvent(type="agent_end"))
        if self._raise_exc is not None:
            raise self._raise_exc


class _FakeCoro:
    """Objekt, das `await`bar ist und einen festen Wert liefert (kein echtes Coroutine-Objekt)."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def __await__(self) -> Any:
        # Generator-Protokoll: yield None als Send-Ziel, dann Wert per RETURN
        # (nicht `raise StopIteration(value)` — PEP 479 convertiert das in
        # RuntimeError "generator raised StopIteration").
        yield None
        return self._value


def make_opts(**over: Any) -> rt.RunEmbeddedPiAgentOptions:
    base: dict[str, Any] = dict(
        prompt="test prompt",
        session_record=SessionRecord(session_id="abc12345", session_key="agent:main:telegram"),
        session_file="/tmp/session.jsonl",
        workspace_dir="/tmp/ws",
        provider="ollama",
        model="llama3.2",
        thinking_level="medium",
        timeout_ms=60_000,
        run_id="run12345",
        skills_snapshot=None,
        config=SelmaConfig(),
        bootstrap_mode="none",
        is_new_session=False,
        abort_signal=None,
    )
    base.update(over)
    return rt.RunEmbeddedPiAgentOptions(**base)


# ════════════════════════════════════════════════════════════
# Layer 1: _BlockChunker
# ════════════════════════════════════════════════════════════


class TestBlockChunker:
    def test_feed_below_min_chars_no_flush(self):
        blocks: list[str] = []
        c = rt._BlockChunker(rt.BlockReplyChunkingConfig(min_chars=10, flush_patterns=["\n\n"]), blocks.append)
        c.feed("short.")
        assert blocks == []  # unter min_chars → nur gepuffert

    def test_feed_empties_at_pattern_boundary(self):
        blocks: list[str] = []
        c = rt._BlockChunker(rt.BlockReplyChunkingConfig(min_chars=5, flush_patterns=["\n\n", ".\n"]), blocks.append)
        c.feed("Hallo.\n\nWie geht's? Weiterer Text...")
        assert blocks == ["Hallo.\n\n"]
        # Rest bleibt im Buffer, bis flush() oder weiteres Pattern
        assert c._buffer == "Wie geht's? Weiterer Text..."

    def test_flush_emits_remaining_buffer(self):
        blocks: list[str] = []
        c = rt._BlockChunker(rt.BlockReplyChunkingConfig(min_chars=999, flush_patterns=["\n\n"]), blocks.append)
        c.feed("Rest ohne Pattern")
        c.flush()
        assert blocks == ["Rest ohne Pattern"]
        assert c._buffer == ""

    def test_flush_with_empty_buffer_is_noop(self):
        blocks: list[str] = []
        c = rt._BlockChunker(rt.BlockReplyChunkingConfig(min_chars=10, flush_patterns=["\n\n"]), blocks.append)
        c.flush()
        assert blocks == []

    def test_flush_with_whitespace_only_buffer_is_noop(self):
        blocks: list[str] = []
        c = rt._BlockChunker(rt.BlockReplyChunkingConfig(min_chars=10, flush_patterns=["\n\n"]), blocks.append)
        c.feed("   \n  ")
        c.flush()
        assert blocks == []

    def test_multiple_blocks_multiple_feeds(self):
        blocks: list[str] = []
        c = rt._BlockChunker(rt.BlockReplyChunkingConfig(min_chars=2, flush_patterns=["\n"]), blocks.append)
        c.feed("a\n")  # min_chars erreicht, letzte Zeile mit Pattern → Block "a\n"
        assert blocks == ["a\n"]
        c.feed("b\n")
        c.flush()  # feed() emittiert pro Call nur einen Block, b\n bleibt im Buffer → flush()
        assert blocks == ["a\n", "b\n"]


# ════════════════════════════════════════════════════════════
# Layer 1: Lifecycle-Events
# ════════════════════════════════════════════════════════════


class TestLifecycleEvents:
    def test_register_and_emit(self):
        events: list[rt.LifecyclePhase] = []
        rt.on_lifecycle_event(events.append)
        ev = rt.LifecyclePhase(run_id="r1", phase="start", started_at=123)
        rt.emit_lifecycle_event(ev)
        assert events == [ev]

    def test_unsubscribe_stops_delivery(self):
        events: list[rt.LifecyclePhase] = []
        unsub = rt.on_lifecycle_event(events.append)
        unsub()
        rt.emit_lifecycle_event(rt.LifecyclePhase(run_id="r1", phase="end", started_at=1, ended_at=2))
        assert events == []

    def test_bad_listener_does_not_block_others(self):
        events: list[rt.LifecyclePhase] = []

        def bad_listener(_ev: Any) -> None:
            raise RuntimeError("boom")

        rt.on_lifecycle_event(bad_listener)
        rt.on_lifecycle_event(events.append)
        ev = rt.LifecyclePhase(run_id="r1", phase="error", started_at=1, error="x")
        rt.emit_lifecycle_event(ev)  # darf nicht werfen
        assert events == [ev]


# ════════════════════════════════════════════════════════════
# Layer 1: _resolve_skills_snapshot / get_session / bootstrap
# ════════════════════════════════════════════════════════════


class TestResolveSkillsSnapshot:
    def test_new_session_builds_snapshot(self, monkeypatch):
        rec = SessionRecord(session_id="id1", session_key="k1")
        built = SkillsSnapshot(version="v2", skill_names=["a"], snapshot_text="txt")
        monkeypatch.setattr(rt, "get_skills_snapshot_version", lambda _ws: "v2")
        monkeypatch.setattr(rt, "build_skill_snapshot", lambda ws, ver: built)
        out = rt._resolve_skills_snapshot(rec, "/ws", is_new_session=True)
        assert out is built
        assert rec.skills_snapshot is built

    def test_stale_version_rebuilds(self, monkeypatch):
        rec = SessionRecord(
            session_id="id1",
            session_key="k1",
            skills_snapshot=SkillsSnapshot(version="v1", skill_names=["old"]),
        )
        built = SkillsSnapshot(version="v2", skill_names=["new"])
        monkeypatch.setattr(rt, "get_skills_snapshot_version", lambda _ws: "v2")
        monkeypatch.setattr(rt, "build_skill_snapshot", lambda ws, ver: built)
        out = rt._resolve_skills_snapshot(rec, "/ws", is_new_session=False)
        assert out is built
        assert rec.skills_snapshot.version == "v2"

    def test_missing_snapshot_rebuilds(self, monkeypatch):
        rec = SessionRecord(session_id="id1", session_key="k1", skills_snapshot=None)
        built = SkillsSnapshot(version="v1", skill_names=[])
        monkeypatch.setattr(rt, "get_skills_snapshot_version", lambda _ws: "v1")
        monkeypatch.setattr(rt, "build_skill_snapshot", lambda ws, ver: built)
        out = rt._resolve_skills_snapshot(rec, "/ws", is_new_session=False)
        assert out is built

    def test_fresh_snapshot_returns_existing(self, monkeypatch):
        existing = SkillsSnapshot(version="v1", skill_names=["a"])
        rec = SessionRecord(session_id="id1", session_key="k1", skills_snapshot=existing)
        monkeypatch.setattr(rt, "get_skills_snapshot_version", lambda _ws: "v1")
        build_calls: list[str] = []
        monkeypatch.setattr(
            rt,
            "build_skill_snapshot",
            lambda ws, ver: build_calls.append(str(ver)),
        )
        out = rt._resolve_skills_snapshot(rec, "/ws", is_new_session=False)
        assert out is existing
        assert build_calls == []  # kein Rebuild


class TestGetSession:
    def _fake_store(self) -> SessionStore:
        return SessionStore(store_path="/tmp/sessions.json")

    def test_new_session_path(self, monkeypatch):
        store = self._fake_store()
        rec = SessionRecord(session_id="new1", session_key="fresh")
        cfg = SelmaConfig()
        seen: dict[str, Any] = {}
        monkeypatch.setattr(rt, "load_session_store", lambda cwd: seen.update(load=cwd) or store)
        monkeypatch.setattr(rt, "resolve_session", lambda s, k, i, c: (rec, True))
        monkeypatch.setattr(rt, "resolve_session_file", lambda r, cwd: f"/tmp/{r.session_id}.jsonl")
        out_store, out_rec, is_new, session_file = rt.get_session("fresh", None, cfg, "/cwd")
        assert out_store is store
        assert is_new is True
        assert session_file == "/tmp/new1.jsonl"
        assert rec.transcript_file == "/tmp/new1.jsonl"  # Transcript-Datei wurde gesetzt
        assert seen["load"] == "/cwd"

    def test_existing_fresh_session_kept(self, monkeypatch):
        store = self._fake_store()
        rec = SessionRecord(session_id="old1", session_key="keep")
        monkeypatch.setattr(rt, "load_session_store", lambda cwd: store)
        monkeypatch.setattr(rt, "resolve_session", lambda s, k, i, c: (rec, False))
        monkeypatch.setattr(rt, "is_session_fresh", lambda r, at_hour, idle_minutes: True)
        monkeypatch.setattr(rt, "resolve_session_file", lambda r, cwd: "/tmp/x.jsonl")
        _s, out_rec, is_new, _f = rt.get_session("keep", None, SelmaConfig(), "/cwd")
        assert out_rec is rec
        assert is_new is False

    def test_stale_session_reset(self, monkeypatch):
        store = self._fake_store()
        old_rec = SessionRecord(session_id="old1", session_key="keep")
        new_rec = SessionRecord(session_id="new2", session_key="keep")
        monkeypatch.setattr(rt, "load_session_store", lambda cwd: store)
        monkeypatch.setattr(rt, "resolve_session", lambda s, k, i, c: (old_rec, False))
        monkeypatch.setattr(rt, "is_session_fresh", lambda r, at_hour, idle_minutes: False)
        monkeypatch.setattr(rt, "reset_session", lambda s, r, cwd: new_rec)
        monkeypatch.setattr(rt, "resolve_session_file", lambda r, cwd: "/tmp/y.jsonl")
        _s, out_rec, is_new, _f = rt.get_session("keep", None, SelmaConfig(), "/cwd")
        assert out_rec is new_rec
        assert is_new is True


class TestDetectBootstrapMode:
    def test_missing_file_is_none(self, tmp_path):
        assert rt.detect_bootstrap_mode(str(tmp_path)) == "none"

    def test_empty_file_is_none(self, tmp_path):
        (tmp_path / "BOOTSTRAP.md").write_text("   \n  \n", encoding="utf-8")
        assert rt.detect_bootstrap_mode(str(tmp_path)) == "none"

    def test_content_is_full(self, tmp_path):
        (tmp_path / "BOOTSTRAP.md").write_text("Los geht es", encoding="utf-8")
        assert rt.detect_bootstrap_mode(str(tmp_path)) == "full"


# ════════════════════════════════════════════════════════════
# Layer 1: agent_command
# ════════════════════════════════════════════════════════════


class TestAgentCommand:
    def _install_fakes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        run_result: rt.AgentCommandResult | None = None,
        run_exc: Exception | None = None,
        deliver: Any = None,
        config: SelmaConfig | None = None,
    ) -> dict[str, Any]:
        """Patches alle agent_command-Abhängigkeiten; liefert Beobachtungs-Dict."""
        state = {
            "run_opts": None,
            "run_calls": 0,
            "delivered": None,
            "lifecycle": [],
        }
        cfg = config or SelmaConfig()
        store = SessionStore(store_path="/tmp/s.json")
        rec = SessionRecord(session_id="rec1", session_key="main:agent:web")
        default_result = run_result or rt.AgentCommandResult(
            payloads=[rt.RunPayload(text="OK")],
            meta=rt.AgentRunMeta(
                session_id="rec1", provider="ollama", model="llama", duration_ms=5, stop_reason="end_turn"
            ),
        )

        class FakeDeliv:
            pass

        async def fake_run(opts: Any) -> Any:
            state["run_calls"] += 1
            state["run_opts"] = opts
            if run_exc is not None:
                raise run_exc
            return default_result

        async def fake_deliver(result: Any, delivery: Any) -> None:
            state["delivered"] = (result, delivery)

        monkeypatch.setattr(rt, "load_config", lambda cwd: cfg)
        monkeypatch.setattr(rt, "get_session", lambda k, i, c, cwd: (store, rec, False, "/tmp/sess.jsonl"))
        monkeypatch.setattr(rt, "detect_bootstrap_mode", lambda ws: "none")
        monkeypatch.setattr(rt, "get_skills_snapshot_version", lambda ws: "v1")
        monkeypatch.setattr(rt, "build_skill_snapshot", lambda ws, ver: SkillsSnapshot(version="v1", skill_names=[]))
        monkeypatch.setattr(rt, "save_session_store", lambda s: None)
        monkeypatch.setattr(
            rt,
            "get_default_model",
            lambda c: ("ollama", "llama-default"),
        )
        monkeypatch.setattr(rt, "resolve_thinking_default", lambda c, p, m: "low")
        monkeypatch.setattr(rt, "resolve_timeout", lambda c: 60)
        monkeypatch.setattr(rt, "resolve_tools_allow", lambda c: None)
        monkeypatch.setattr(rt, "run_embedded_pi_agent", fake_run)
        monkeypatch.setattr(rt, "deliver_result", deliver or fake_deliver)
        monkeypatch.setattr(
            rt,
            "update_session_store_after_run",
            lambda *, store, session_record, provider, model: state.update(override=(provider, model)),
        )
        rt._LIFECYCLE_LISTENERS.add(state["lifecycle"].append)
        return state

    def test_validation_empty_message(self, monkeypatch, capsys):
        self._install_fakes(monkeypatch)
        with pytest.raises(ValueError, match="empty"):
            run(rt.agent_command("   ", session_key="k1"))

    def test_validation_missing_session_key_and_id(self, monkeypatch):
        self._install_fakes(monkeypatch)
        with pytest.raises(ValueError, match="session_key or session_id"):
            run(rt.agent_command("Moin"))

    def test_happy_path_full_result(self, monkeypatch):
        state = self._install_fakes(monkeypatch)
        delivery = rt.DeliveryContext(deliver=True, reply_channel="web")
        rec = SessionRecord(session_id="rec1", session_key="k", thinking_level="high", model_override="custom")
        # rec mit Overrides injizieren, um Provider/Model-Wahl zu testen
        monkeypatch.setattr(
            rt,
            "get_session",
            lambda k, i, c, cwd: (SessionStore(store_path="/tmp/s.json"), rec, True, "/tmp/s.jsonl"),
        )
        result = run(rt.agent_command("Moin", session_key="k", delivery=delivery, runtime=rt.RuntimeEnv(cwd="/cwd")))
        assert result.payloads[0].text == "OK"
        assert state["run_calls"] == 1
        opts = state["run_opts"]
        assert opts.provider == "ollama"
        assert opts.model == "custom"  # model_override aus SessionRecord
        assert opts.thinking_level == "high"  # thinking aus SessionRecord
        assert opts.timeout_ms == 60_000
        assert opts.prompt == "Moin"
        assert state["delivered"] is not None
        assert state["override"] == ("ollama", "custom")
        phases = [e.phase for e in state["lifecycle"]]
        assert phases == ["start", "end"]
        assert state["lifecycle"][-1].ended_at is not None

    def test_exception_emits_error_lifecycle_and_raises(self, monkeypatch):
        state = self._install_fakes(monkeypatch, run_exc=RuntimeError("kaputt"))
        with pytest.raises(RuntimeError, match="kaputt"):
            run(rt.agent_command("Moin", session_key="k"))
        phases = [e.phase for e in state["lifecycle"]]
        assert phases == ["start", "error"]
        assert state["lifecycle"][-1].error == "kaputt"
        assert state["delivered"] is None  # kein Delivery nach Fehler
        assert "override" not in state  # kein After-Run-Store-Update nach Fehler

    def test_timeout_zero_is_forwarded(self, monkeypatch):
        state = self._install_fakes(monkeypatch)
        monkeypatch.setattr(rt, "resolve_timeout", lambda c: 0)
        run(rt.agent_command("Moin", session_key="k"))
        assert state["run_opts"].timeout_ms == 0


# ════════════════════════════════════════════════════════════
# Layer 2: pick_fallback_thinking_level
# ════════════════════════════════════════════════════════════


class TestPickFallbackThinkingLevel:
    def test_none_current_returns_none(self):
        assert rt.pick_fallback_thinking_level(None, set()) is None

    def test_high_falls_to_medium(self):
        out = rt.pick_fallback_thinking_level("high", {"high"})
        assert out == "medium"

    def test_high_falls_directly_to_low_if_medium_attempted(self):
        out = rt.pick_fallback_thinking_level("high", {"high", "medium"})
        assert out == "low"

    def test_low_exhausted_returns_none(self):
        assert rt.pick_fallback_thinking_level("low", {"high", "medium", "low"}) is None

    def test_all_attempted_returns_none(self):
        assert rt.pick_fallback_thinking_level("medium", {"high", "medium", "low"}) is None

    def test_none_attempted_still_returns_lower_level(self):
        # attempted leer → erste Stufe unter current
        assert rt.pick_fallback_thinking_level("high", set()) == "medium"
        assert rt.pick_fallback_thinking_level("medium", set()) == "low"

    def test_invalid_level_returns_none(self):
        out = rt.pick_fallback_thinking_level(  # type: ignore[arg-type]
            "ultra-turbo",
            set(),
        )
        assert out is None


# ════════════════════════════════════════════════════════════
# Layer 2: handle_aborted / repair_* / _error_result
# ════════════════════════════════════════════════════════════


class HandleAborted:
    def test_aborted_result_shape(self):
        opts = make_opts()
        result = rt.handle_aborted(opts)
        assert result.payloads[0].is_error is True
        assert result.payloads[0].text == "Run was aborted."
        assert result.meta.aborted is True
        assert result.meta.stop_reason == "aborted"
        assert result.meta.session_id == "abc12345"
        assert result.meta.provider == "ollama"
        assert result.meta.model == "llama3.2"
        assert result.meta.duration_ms == 0


class TestRepairThinkingNotSupported:
    def test_retry_with_fallback(self):
        opts = make_opts()
        state = rt.LoopState(active_thinking="high", attempted_thinking={"high"})
        should_retry, error_result = rt.repair_thinking_not_supported(opts, state)
        assert should_retry is True
        assert error_result is None
        assert state.active_thinking == "medium"

    def test_no_fallback_returns_error_result(self):
        opts = make_opts()
        state = rt.LoopState(active_thinking="low", attempted_thinking={"high", "medium", "low"})
        should_retry, error_result = rt.repair_thinking_not_supported(opts, state)
        assert should_retry is False
        assert error_result is not None
        assert error_result.payloads[0].is_error is True
        assert error_result.meta.stop_reason == "thinking_not_supported"


class TestRepairContextOverflow:
    def _patch_flush(self, monkeypatch, tmp_path):
        flushed: list[tuple[str, str]] = []

        async def fake_flush(key: str, cwd: str) -> None:
            flushed.append((key, cwd))

        monkeypatch.setattr(rt, "memory_flush", fake_flush)
        return flushed

    def test_limit_reached_gives_up(self, monkeypatch, tmp_path):
        self._patch_flush(monkeypatch, tmp_path)
        compacted = []
        monkeypatch.setattr(rt, "compact_session", lambda **kw: compacted.append(kw))
        opts = make_opts()
        state = rt.LoopState(compaction_attempts=rt.MAX_OVERFLOW_COMPACTION_ATTEMPTS)
        err = rt.AttemptError(kind="context_overflow", message="ctx")
        should_retry, result = run(rt.repair_context_overflow(opts, err, state))
        assert should_retry is False
        assert state.compaction_attempts == rt.MAX_OVERFLOW_COMPACTION_ATTEMPTS  # nicht erhöht
        assert compacted == []  # keine Kompaktierung nach Limit
        assert result is not None
        assert result.meta.stop_reason == "context_overflow"
        assert "too long" in result.payloads[0].text

    def test_compaction_success_retries(self, monkeypatch, tmp_path):
        self._patch_flush(monkeypatch, tmp_path)

        class CompactOk:
            compacted = True
            tokens_before = 5000
            tokens_after = 2000
            reason = ""

        calls: dict[str, Any] = {}

        async def fake_compact(*, session_file: Any, config: Any) -> Any:
            calls["session_file"] = session_file
            calls["config"] = config
            return CompactOk()

        monkeypatch.setattr(rt, "compact_session", fake_compact)
        opts = make_opts(session_file="/tmp/abc.jsonl")
        state = rt.LoopState()
        err = rt.AttemptError(kind="context_overflow", message="too big")
        should_retry, result = run(rt.repair_context_overflow(opts, err, state))
        assert should_retry is True
        assert result is None
        assert state.compaction_attempts == 1
        assert calls["session_file"] == "/tmp/abc.jsonl"
        # memory_flush wurde vor der Kompaktierung aufgerufen
        assert state.compaction_attempts == 1

    def test_compaction_no_reduction_gives_up(self, monkeypatch, tmp_path):
        self._patch_flush(monkeypatch, tmp_path)

        class CompactNo:
            compacted = False
            tokens_before = 100
            tokens_after = 100
            reason = "Nichts zu komprimieren"

        monkeypatch.setattr(
            rt,
            "compact_session",
            lambda **kw: _FakeCoro(CompactNo()),
        )
        opts = make_opts()
        state = rt.LoopState()
        err = rt.AttemptError(kind="context_overflow", message="ctx")
        should_retry, result = run(rt.repair_context_overflow(opts, err, state))
        assert should_retry is False
        assert state.compaction_attempts == 1
        assert result is not None
        assert result.meta.stop_reason == "context_overflow"
        assert "not possible" in result.payloads[0].text


class TestMemoryFlush:
    def test_memory_flush_calls_agent_command(self, monkeypatch):
        args: list[Any] = {}

        async def fake_agent_command(prompt: str, **kw: Any) -> Any:
            args["prompt"] = prompt
            args["kw"] = kw
            return None

        monkeypatch.setattr(rt, "agent_command", fake_agent_command)
        run(rt.memory_flush("mykey", "/cwd-x"))
        assert "Memory Flush" in args["prompt"]
        assert "memory/" in args["prompt"]
        assert args["kw"]["session_key"] == "mykey"
        assert args["kw"]["runtime"].cwd == "/cwd-x"

    def test_memory_flush_swallows_agent_errors(self, monkeypatch):
        async def fake_agent_command(prompt: str, **kw: Any) -> Any:
            raise RuntimeError("model down")

        monkeypatch.setattr(rt, "agent_command", fake_agent_command)
        run(rt.memory_flush("mykey", "/cwd-x"))  # kein Raise


# ════════════════════════════════════════════════════════════
# Layer 2: run_embedded_pi_agent
# ════════════════════════════════════════════════════════════


class _FakeAttempt:
    def __init__(self, error: rt.AttemptError | None = None, result: Any = None):
        self.error = error
        self.result = result


class TestRunEmbeddedPiAgent:
    def success_result(self, message: str = "fetched") -> rt.AgentCommandResult:
        return rt.AgentCommandResult(
            payloads=[rt.RunPayload(text=message)],
            meta=rt.AgentRunMeta(
                session_id="abc12345", provider="ollama", model="llama3.2", duration_ms=1, stop_reason="end_turn"
            ),
        )

    def test_success_returns_directly(self, monkeypatch):
        expected = self.success_result()

        async def fake_attempt(opts: Any) -> Any:
            return _FakeAttempt(result=expected)

        monkeypatch.setattr(rt, "run_embedded_attempt", fake_attempt)
        out = run(rt.run_embedded_pi_agent(make_opts()))
        assert out is expected

    def _patch_retry_sequence(self, monkeypatch, attempts: list[Any], patches: list[Any]) -> list[Any]:
        """Jeder Lauf der repair-Sequenz bekommt den nächsten Eintrag in `attempts`."""
        state: dict[str, Any] = {"i": 0}

        async def fake_attempt(opts: Any) -> Any:
            i = state["i"]
            state["i"] += 1
            if patches and i < len(patches):
                patches[i]()
            if i < len(attempts):
                return attempts[i]
            raise AssertionError(f"mehr Attempts als erwartet ({i})")

        monkeypatch.setattr(rt, "run_embedded_attempt", fake_attempt)
        return patches

    def test_context_overflow_repair_then_success(self, monkeypatch):
        ok = self.success_result()
        attempts = [
            _FakeAttempt(error=rt.AttemptError(kind="context_overflow", message="ctx")),
            _FakeAttempt(result=ok),
        ]
        monkeypatch.setattr(rt, "repair_context_overflow", lambda opts, err, state: _FakeCoro((True, None)))

        async def fake_attempt(opts: Any) -> Any:
            return attempts.pop(0)

        monkeypatch.setattr(rt, "run_embedded_attempt", fake_attempt)
        out = run(rt.run_embedded_pi_agent(make_opts()))
        assert out is ok

    def test_thinking_not_supported_repair_then_success(self, monkeypatch):
        ok = self.success_result()
        attempts: list[Any] = [
            _FakeAttempt(error=rt.AttemptError(kind="thinking_not_supported", message="nope")),
            _FakeAttempt(result=ok),
        ]
        seen: list[Any] = []

        def fake_repair(opts: Any, state: Any) -> Any:
            seen.append(state.active_thinking)
            state.active_thinking = "low"
            return True, None

        monkeypatch.setattr(rt, "repair_thinking_not_supported", fake_repair)

        async def fake_attempt(opts: Any) -> Any:
            return attempts.pop(0)

        monkeypatch.setattr(rt, "run_embedded_attempt", fake_attempt)
        opts = make_opts()
        out = run(rt.run_embedded_pi_agent(opts))
        assert out is ok
        # Beim zweiten Attempt muss die herabgestufte thinking_level anliegen
        # (via opts.model_copy(update=...)) — hier nur: Repair wurde aufgerufen
        assert len(seen) == 1

    def test_thinking_fallback_exhausted_returns_error(self, monkeypatch):
        error_res = rt.AgentCommandResult(
            payloads=[rt.RunPayload(text="thinking dead", is_error=True)],
            meta=rt.AgentRunMeta(
                session_id="abc12345", provider="ollama", model="m", duration_ms=0, stop_reason="thinking_not_supported"
            ),
        )
        called: list[Any] = []

        def fake_repair(opts: Any, state: Any) -> Any:
            called.append(opts.run_id)
            return False, error_res

        monkeypatch.setattr(rt, "repair_thinking_not_supported", fake_repair)

        async def fake_attempt(opts: Any) -> Any:
            return _FakeAttempt(error=rt.AttemptError(kind="thinking_not_supported", message="x"))

        monkeypatch.setattr(rt, "run_embedded_attempt", fake_attempt)
        out = run(rt.run_embedded_pi_agent(make_opts()))
        assert out is error_res

    def test_aborted_returns_error_result(self, monkeypatch):
        async def fake_attempt(opts: Any) -> Any:
            return _FakeAttempt(error=rt.AttemptError(kind="aborted", message="timeout"))

        monkeypatch.setattr(rt, "run_embedded_attempt", fake_attempt)
        out = run(rt.run_embedded_pi_agent(make_opts()))
        assert out.payloads[0].is_error is True
        assert out.meta.aborted is True
        assert out.meta.stop_reason == "aborted"

    def test_unknown_error_kind_raises_runtime_error(self, monkeypatch):
        async def fake_attempt(opts: Any) -> Any:
            return _FakeAttempt(
                error=rt.AttemptError(kind="other", message="mysteriöser crash"),  # type: ignore[arg-type]
            )

        monkeypatch.setattr(rt, "run_embedded_attempt", fake_attempt)
        with pytest.raises(RuntimeError, match="mysteriöser crash"):
            run(rt.run_embedded_pi_agent(make_opts()))

    def test_attempt_thinking_level_propagated_via_model_copy(self, monkeypatch):
        """run_embedded_attempt erhält opts mit der aktuellen active_thinking."""
        received: list[Any] = []

        async def fake_attempt(opts: Any) -> Any:
            received.append(opts)
            return _FakeAttempt(result=self.success_result())

        monkeypatch.setattr(rt, "run_embedded_attempt", fake_attempt)
        run(rt.run_embedded_pi_agent(make_opts()))
        assert received and received[0].thinking_level == "medium"


# ════════════════════════════════════════════════════════════
# Layer 3: detect_attempt_error
# ════════════════════════════════════════════════════════════


class TestDetectAttemptError:
    @pytest.mark.parametrize(
        "needle",
        [
            "exceeded context length",
            "context window exceeded",
            "maximum context size",
            "token limit reached",
            "prompt is too long for model",
        ],
    )
    def test_exception_context_overflow_keywords(self, needle):
        err = rt.detect_attempt_error(
            final_text=None,
            exception=Exception(f"Error: {needle}."),
        )
        assert err is not None
        assert err.kind == "context_overflow"
        assert needle in err.message

    @pytest.mark.parametrize(
        "needle",
        [
            "invalid reasoning_effort",
            "thinking is not supported for this model",
            "model does not support thinking",
        ],
    )
    def test_exception_thinking_not_supported_keywords(self, needle):
        err = rt.detect_attempt_error(
            final_text=None,
            exception=Exception(f"Error: {needle}, try low"),
        )
        assert err is not None
        assert err.kind == "thinking_not_supported"
        assert err.rejected_thinking_level == "low"

    def test_exception_takes_priority_over_text(self):
        # Text hat ein *anderes* Keyword; Exception soll gewinnen
        err = rt.detect_attempt_error(
            final_text="token limit",
            exception=Exception("context window blew up"),
        )
        assert err is not None
        assert err.kind == "context_overflow"
        assert "context window" in err.message

    def test_text_only_context_overflow(self):
        err = rt.detect_attempt_error(
            final_text="The model said: prompt is too long, sorry.",
            exception=None,
        )
        assert err is not None
        assert err.kind == "context_overflow"

    def test_text_only_thinking_not_supported_extracts_level(self):
        err = rt.detect_attempt_error(
            final_text="reasoning_effort medium not available — use high, low, or none",
            exception=None,
        )
        assert err is not None
        assert err.kind == "thinking_not_supported"
        # _extract_rejected_level prüft _THINKING_LEVELS = ["low", "medium", "high"] —
        # "low" wird zuerst gefunden (steht auch im Text)
        assert err.rejected_thinking_level in {"high", "medium", "low"}
        assert err.rejected_thinking_level == "low"

    def test_no_error(self):
        assert rt.detect_attempt_error(final_text="Alles gut, hier die Antwort.", exception=None) is None
        assert rt.detect_attempt_error(final_text=None, exception=None) is None

    def test_harmless_exception_not_matched(self):
        err = rt.detect_attempt_error(
            final_text=None,
            exception=RuntimeError("connection reset by peer"),
        )
        assert err is None

    def test_extract_rejected_level_none_when_absent(self):
        err = rt.detect_attempt_error(
            final_text="reasoning_effort is invalid here (no level names)",
            exception=None,
        )
        assert err is not None
        assert err.rejected_thinking_level is None


# ════════════════════════════════════════════════════════════
# Layer 3: subscribe_output_collector
# ════════════════════════════════════════════════════════════


class TestSubscribeOutputCollector:
    def test_collects_message_and_agent_end(self):
        partials: list[str] = []
        dlv = rt.DeliveryContext(on_partial_reply=partials.append)
        session = FakeSession(message="Hallo!")
        out, unsub = rt.subscribe_output_collector(dlv, session)
        session.prompt_sync_emit() if hasattr(session, "prompt_sync_emit") else None
        # prompt auslösen, um Events zu emittieren
        run(session.prompt("irrelevant"))
        assert out.final_text == "Hallo!"
        assert partials == ["Hallo!"]
        unsub()
        assert session.subscribers == []

    def test_block_chunking_and_flush(self):
        blocks: list[str] = []
        flushes: list[str] = []
        dlv = rt.DeliveryContext(
            on_block_reply=blocks.append,
            on_block_reply_flush=lambda: flushes.append("flushed"),
            block_reply_chunking=rt.BlockReplyChunkingConfig(min_chars=2, flush_patterns=["!"]),
        )
        # feed() emitiert max. EIN Block pro Aufruf (bei rfind-Match) —
        # darum zwei getrennte message_update-Events, die auch Buffering
        # über mehrere Events hinweg abdecken.
        session = FakeSession(message=None)
        rt.subscribe_output_collector(dlv, session)
        assert len(session.subscribers) == 1
        handler = session.subscribers[0]
        handler(AgentEvent(type="message_update", payload="Er"))  # Buffer < min_chars, kein Pattern
        handler(AgentEvent(type="message_update", payload="ste!"))  # Buffer "Erste!", '!' → Block
        handler(AgentEvent(type="message_update", payload=" Zweite!"))  # '!' → Block
        handler(AgentEvent(type="agent_end"))  # Flush (leerer Rest) + on_block_reply_flush
        assert blocks == ["Erste!", " Zweite!"]
        assert flushes == ["flushed"]

    def test_tool_start_callback_with_payload_object(self):
        tool_calls: list[Any] = []
        dlv = rt.DeliveryContext(on_tool_call=lambda name, args: tool_calls.append((name, args)))
        session = FakeSession(message=None)
        out, _ = rt.subscribe_output_collector(dlv, session)
        tool_event = AgentEvent(
            type="tool_start", payload=types.SimpleNamespace(name="read", arguments={"path": "f.txt"})
        )
        for sub in session.subscribers:
            sub(tool_event)
        assert out.tool_names_used == ["read"]
        assert tool_calls == [("read", {"path": "f.txt"})]

    def test_tool_start_with_bare_string_payload(self):
        tool_calls: list[Any] = []
        dlv = rt.DeliveryContext(on_tool_call=lambda name, args: tool_calls.append(name))
        session = FakeSession(message=None)
        out, _ = rt.subscribe_output_collector(dlv, session)
        for sub in session.subscribers:
            sub(AgentEvent(type="tool_start", payload="exec"))
        assert out.tool_names_used == ["exec"]

    def test_no_callbacks_means_still_collected(self):
        dlv = rt.DeliveryContext()
        session = FakeSession(message="still collected")
        out, _ = rt.subscribe_output_collector(dlv, session)
        run(session.prompt("x"))
        assert out.final_text == "still collected"

    def test_unsubscribe_returns_session(self):
        dlv = rt.DeliveryContext()
        session = FakeSession(message=None)
        _out, unsub = rt.subscribe_output_collector(dlv, session)
        assert len(session.subscribers) == 1
        unsub()
        assert session.subscribers == []


# ════════════════════════════════════════════════════════════
# Layer 3: execute_prompt
# ════════════════════════════════════════════════════════════


class TestExecutePrompt:
    def test_success_no_timeout(self):
        session = FakeSession(message="ok")
        res = run(rt.execute_prompt(session, "hi", None, 60_000, "run1"))
        assert res.aborted is False
        assert res.timed_out is False
        assert res.run_exception is None
        assert session.prompt_calls == ["hi"]

    def test_abort_signal_pre_set_short_circuits(self):
        session = FakeSession(message="ok")
        sig = asyncio.Event()
        sig.set()
        res = run(rt.execute_prompt(session, "hi", sig, 60_000, "run1"))
        assert res.aborted is True
        assert session.prompt_calls == []  # prompt NICHT aufgerufen

    def test_timeout_marks_timed_out_and_aborted(self):
        session = FakeSession(message="ok", timeout_seconds=5.0)
        res = run(rt.execute_prompt(session, "hi", None, 40, "run1"))  # 40ms Timeout
        assert res.aborted is True
        assert res.timed_out is True
        assert res.run_exception is None

    def test_cancellation_error_marks_aborted(self):
        session = FakeSession(raise_exc=asyncio.CancelledError())

        async def scenario() -> Any:
            return await rt.execute_prompt(session, "hi", None, 0, "run1")  # timeout 0 → direkt await

        try:
            run(scenario())
        except asyncio.CancelledError:
            pass  # Python 3.11+: CancelledError von wait_for propagiert manchmal
        # PromptResult trotzdem mit aborted=True
        res = run(
            rt.execute_prompt(
                FakeSession(raise_exc=asyncio.CancelledError()),
                "hi",
                None,
                0,
                "run2",
            )
        )
        assert res.aborted is True

    def test_generic_exception_captured(self):
        session = FakeSession(raise_exc=ValueError("exploded"))
        res = run(rt.execute_prompt(session, "hi", None, 0, "run1"))
        assert res.aborted is False
        assert res.run_exception is not None
        assert isinstance(res.run_exception, ValueError)


# ════════════════════════════════════════════════════════════
# Layer 3: build_attempt_result
# ════════════════════════════════════════════════════════════


class TestBuildAttemptResult:
    def test_timeout_abort(self):
        opts = make_opts()
        out = rt.CollectedOutput(text_parts=["partial"])
        res = rt.build_attempt_result(
            rt.PromptResult(aborted=True, timed_out=True),
            out,
            FakeSession(message_count=3),
            opts,
            42,
        )
        assert res.error is not None
        assert res.error.kind == "aborted"
        assert res.error.message == "Timeout"
        assert res.message_count == 3
        assert res.result is None

    def test_signal_abort(self):
        opts = make_opts()
        res = rt.build_attempt_result(
            rt.PromptResult(aborted=True),
            rt.CollectedOutput(),
            FakeSession(message_count=1),
            opts,
            10,
        )
        assert res.error is not None
        assert res.error.message == "Aborted"

    def test_known_error_in_final_text(self):
        opts = make_opts()
        out = rt.CollectedOutput(text_parts=["model said: context window is full"])
        res = rt.build_attempt_result(
            rt.PromptResult(),
            out,
            FakeSession(message_count=5),
            opts,
            100,
        )
        assert res.error is not None
        assert res.error.kind == "context_overflow"
        assert res.message_count == 5

    def test_run_exception_with_known_error_wins_over_raise(self):
        # detect_attempt_error findet bekanntes Muster in Exception → AttemptError statt Raise
        opts = make_opts()
        res = rt.build_attempt_result(
            rt.PromptResult(run_exception=RuntimeError("maximum context length exceeded")),
            rt.CollectedOutput(),
            FakeSession(message_count=2),
            opts,
            10,
        )
        assert res.error is not None
        assert res.error.kind == "context_overflow"

    def test_unknown_run_exception_raises(self):
        opts = make_opts()
        with pytest.raises(ValueError, match="boom"):
            rt.build_attempt_result(
                rt.PromptResult(run_exception=ValueError("boom")),
                rt.CollectedOutput(),
                FakeSession(message_count=1),
                opts,
                1,
            )

    def test_success_shape(self):
        opts = make_opts()
        out = rt.CollectedOutput(text_parts=["Hallo", " Welt"])
        res = rt.build_attempt_result(
            rt.PromptResult(),
            out,
            FakeSession(session_id="sess-local", message_count=7),
            opts,
            99,
        )
        assert res.error is None
        assert res.result is not None
        assert res.result.payloads[0].text == "Hallo Welt"
        assert res.result.meta.session_id == "sess-local"  # Session-ID schlägt Record-ID
        assert res.result.meta.provider == "ollama"
        assert res.result.meta.model == "llama3.2"
        assert res.result.meta.duration_ms == 99
        assert res.result.meta.aborted is False
        assert res.result.meta.stop_reason == "end_turn"
        assert res.message_count == 7

    def test_success_falls_back_to_record_session_id(self):
        opts = make_opts()
        res = rt.build_attempt_result(
            rt.PromptResult(),
            rt.CollectedOutput(text_parts=["hallo"]),
            FakeSession(session_id=None),
            opts,
            1,
        )
        assert res.result is not None
        assert res.result.meta.session_id == "abc12345"  # aus SessionRecord


# ════════════════════════════════════════════════════════════
# Layer 3: run_embedded_attempt
# ════════════════════════════════════════════════════════════


class TestRunEmbeddedAttempt:
    def _install(
        self, monkeypatch: pytest.MonkeyPatch, session: FakeSession, tools: list[AgentTool] | None = None
    ) -> types.SimpleNamespace:
        state: types.SimpleNamespace = types.SimpleNamespace(
            session=session, system_prompt=None, prompt_prefix=None, loaded_contexts=[]
        )
        monkeypatch.setattr(
            rt,
            "create_agent_session",
            lambda options: _async_returning(session),
        )
        monkeypatch.setattr(
            rt, "create_selma_tools", lambda cwd, config=None: tools or [make_tool(n) for n in ["read", "write"]]
        )
        monkeypatch.setattr(
            rt,
            "build_agent_system_prompt",
            lambda params: (setattr(state, "system_prompt", "SYSTEM-PROMPT"), "SYSTEM-PROMPT")[1],
        )
        monkeypatch.setattr(rt, "build_agent_user_prompt_prefix", lambda mode: "BOOTSTRAP-PFX")
        monkeypatch.setattr(rt, "_load_context_files", lambda ws: state.loaded_contexts)

        return state

    def test_happy_path_end_to_end(self, monkeypatch, tmp_path):
        session = FakeSession(message="Antwort!", session_id="run-sess")
        self._install(monkeypatch, session)
        opts = make_opts(session_file=str(tmp_path / "session.jsonl"), workspace_dir=str(tmp_path))
        result = run(rt.run_embedded_attempt(opts))
        assert result.error is None
        assert result.result is not None
        assert result.result.payloads[0].text == "Antwort!"
        assert session.prompt_calls and session.prompt_calls[0] == "test prompt"

    def test_bootstrap_prefix_injected_for_new_session(self, monkeypatch, tmp_path):
        session = FakeSession(message="Hallo")
        self._install(monkeypatch, session)
        opts = make_opts(
            is_new_session=True,
            session_file=str(tmp_path / "session.jsonl"),
            workspace_dir=str(tmp_path),
            bootstrap_mode="full",
        )
        run(rt.run_embedded_attempt(opts))
        assert session.prompt_calls[0].startswith("BOOTSTRAP-PFX\n\ntest prompt")

    def test_no_bootstrap_prefix_for_existing_session(self, monkeypatch, tmp_path):
        session = FakeSession(message="Hallo")
        self._install(monkeypatch, session)
        opts = make_opts(
            is_new_session=False,
            session_file=str(tmp_path / "session.jsonl"),
            workspace_dir=str(tmp_path),
            bootstrap_mode="full",
        )
        run(rt.run_embedded_attempt(opts))
        assert session.prompt_calls[0] == "test prompt"  # Kein Prefix

    def test_tools_allow_filters_tools(self, monkeypatch, tmp_path):
        session = FakeSession(message="ok")
        tools = [make_tool("read"), make_tool("write"), make_tool("exec")]
        self._install(monkeypatch, session, tools=tools)
        captured_tools: list[Any] = []

        async def fake_create(options: Any) -> FakeSession:
            captured_tools.extend(options.tools)
            return session

        monkeypatch.setattr(rt, "create_agent_session", fake_create)
        opts = make_opts(tools_allow=["read"], session_file=str(tmp_path / "s.jsonl"), workspace_dir=str(tmp_path))
        run(rt.run_embedded_attempt(opts))
        assert [t.name for t in captured_tools] == ["read"]

    def test_context_files_loaded(self, monkeypatch, tmp_path):
        session = FakeSession(message="ok")
        state = self._install(monkeypatch, session)
        context_files = [rt.EmbeddedContextFile(path="AGENTS.md", content="ctx")]
        state.loaded_contexts = context_files
        passed = []

        def fake_build(params: Any) -> str:
            passed.append(params)
            return "SP"

        monkeypatch.setattr(rt, "build_agent_system_prompt", fake_build)
        opts = make_opts(session_file=str(tmp_path / "s.jsonl"), workspace_dir=str(tmp_path))
        run(rt.run_embedded_attempt(opts))
        assert passed[0].context_files == context_files
        assert passed[0].context_files[0] is context_files[0]

    def test_tools_allow_none_uses_all(self, monkeypatch, tmp_path):
        session = FakeSession(message="ok")
        tools = [make_tool("a"), make_tool("b")]
        self._install(monkeypatch, session, tools=tools)
        captured = []

        async def fake_create(options: Any) -> FakeSession:
            captured.extend(options.tools)
            return session

        monkeypatch.setattr(rt, "create_agent_session", fake_create)
        opts = make_opts(session_file=str(tmp_path / "s.jsonl"), workspace_dir=str(tmp_path))
        run(rt.run_embedded_attempt(opts))
        assert [t.name for t in captured] == ["a", "b"]

    def test_unsubscribe_called_even_on_prompt_error(self, monkeypatch, tmp_path):
        session = FakeSession(raise_exc=RuntimeError("prompt crashed"))
        self._install(monkeypatch, session)
        opts = make_opts(session_file=str(tmp_path / "s.jsonl"), workspace_dir=str(tmp_path))
        # execute_prompt fängt die Exception in result.run_exception (kein Raise dort),
        # aber build_attempt_result RAISED sie (unbekanntes Muster → raise run_exception).
        # Der unsubscribe()-Call im finally-Block von run_embedded_attempt darf NICHT
        # übersprungen werden — das ist der eigentliche Punkt dieses Tests.
        with pytest.raises(RuntimeError, match="prompt crashed"):
            run(rt.run_embedded_attempt(opts))
        assert session.subscribers == []  # unsubscribed trotz Raise


async def _async_returning(value: Any) -> Any:
    return value
