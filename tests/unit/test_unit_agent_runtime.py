# ============================================================
# test_unit_agent_runtime.py
# Deckt src/selma/agent_runtime.py ab:
#   RunResult / RunParams, RunLaneManager, SessionFactory,
#   SystemPromptBuilder (light + full + Truncation),
#   EventSubscriber (alle Event-Typen), RunOrchestrator
#   (ok / timeout / error / Lane-Serialisierung / Cache-Hit).
# Stil: sync-Tests + run(coro)-Helper (frische Event-Loops),
# Fakes über mock.patch (kein pytest-asyncio).
# ============================================================

from __future__ import annotations

import asyncio
import logging
import types
import unittest.mock as mock
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, cast

import pytest

from selma import agent_runtime as ar
from selma.agent import AgentEvent, AgentOptions, AgentTool, ToolSchema
from selma.agent_session import CreateSessionOptions
from selma.data import NormalizedTurnInput
from selma.my_system_prompt import BuildSystemPromptOptions, ContextFile


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _base_prompt(_options: BuildSystemPromptOptions) -> str:
    return "BASE"


def _build_returning(prompt: str) -> Callable[..., str]:
    def _build(**kw: Any) -> str:
        return prompt

    return _build


def make_tool(name: str = "t1") -> AgentTool:
    return AgentTool(
        name=name,
        description="test tool",
        parameters=ToolSchema(type="object", properties={}),
        execute=lambda *a, **kw: None,
    )


# ── Fake für AgentSession ─────────────────────────────────────


class FakeAgentOptions(AgentOptions):
    def __init__(self) -> None:
        super().__init__(model="fake", system_prompt="")


class FakeAgent:
    def __init__(self) -> None:
        self._options = FakeAgentOptions()


class FakeSession:
    def __init__(
        self, prompt_delay: float = 0.0, raise_exc: Exception | None = None, final_reply: str | None = None
    ) -> None:
        self.agent = FakeAgent()
        self._prompt_delay = prompt_delay
        self._raise_exc = raise_exc
        self._final_reply = final_reply
        self.subscribed: list[Callable[[AgentEvent], None]] = []
        self.unsubscribed = 0
        self.prompt_calls: list[str] = []
        self.timeline: list[tuple[str, float]] = []

    def subscribe(self, listener: Callable[[AgentEvent], None]) -> Callable[[], None]:
        self.subscribed.append(listener)

        def unsubscribe() -> None:
            self.unsubscribed += 1

        return unsubscribe

    async def prompt(self, text: str) -> None:
        self.prompt_calls.append(text)
        t = asyncio.get_running_loop().time()
        self.timeline.append(("start", t))
        if self._prompt_delay:
            await asyncio.sleep(self._prompt_delay)
        self.timeline.append(("end", asyncio.get_running_loop().time()))
        if self._raise_exc is not None:
            raise self._raise_exc
        # Im echten Lauf liefert die Agent-Session das message_end-Event
        # WÄHREND prompt() — dann liest der Orchestrator final_reply.
        # Der Listener ist synchron (EventSubscriber.get_listener()).
        if self._final_reply is not None:
            for listener in list(self.subscribed):
                listener(AgentEvent(type="message_end", payload=types.SimpleNamespace(content=self._final_reply)))


# ── Fake-Factory mit echter Cache-Logik ──────────────────────


class CacheFactory:
    """Simuliert SessionFactory.get_or_create inkl. Cache-Pfad."""

    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self._cache: dict[str, FakeSession] = {}
        self.calls: list[str] = []

    async def get_or_create(
        self,
        session_key: str,
        workspace_dir: str,
        system_prompt: str,
        tools: list[AgentTool],
        model: str | None,
    ) -> FakeSession:
        self.calls.append(session_key)
        if session_key in self._cache:
            self._cache[session_key].agent._options.system_prompt = system_prompt
            return self._cache[session_key]
        self.session.agent._options.system_prompt = system_prompt
        self._cache[session_key] = self.session
        return self.session

    def invalidate(self, session_key: str) -> None:
        self._cache.pop(session_key, None)


def _captured_spawn() -> tuple[list[Coroutine[Any, Any, Any]], Callable[[Coroutine[Any, Any, Any]], object]]:
    coros: list[Coroutine[Any, Any, Any]] = []

    def fake_spawn(coro: Coroutine[Any, Any, Any]) -> object:
        coros.append(coro)
        return object()

    return coros, fake_spawn


def simple_ctx(**kw: Any) -> NormalizedTurnInput:
    d = dict(body="x", session_key="sk-1", provider=None, chat_type=None, sender_name=None)
    d.update(kw)
    # Duck-Type-Double f\u00fcr NormalizedTurnInput (nur die im Test genutzten Felder)
    return cast(NormalizedTurnInput, types.SimpleNamespace(**d))


# ── Modelle ───────────────────────────────────────────────────


class TestRunResultModel:
    def test_fields_and_defaults(self) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        r = ar.RunResult(run_id="abc123", session_key="sk", status="ok", reply="hi", started_at=now, ended_at=now)
        assert r.error is None

        r2 = ar.RunResult(
            run_id="abc", session_key="sk", status="error", reply=None, started_at=now, ended_at=now, error="boom"
        )
        assert r2.error == "boom"

    def test_invalid_status_rejected(self) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        # Pydantic-Validierung → ValidationError (ist ein ValueError)
        with pytest.raises(ValueError):
            ar.RunResult(
                run_id="x",
                session_key="s",
                status="bogus",  # type: ignore[arg-type]  # bewusst ung\u00fcltig, testet Pydantic-Validierung
                reply=None,
                started_at=now,
                ended_at=now,
            )


class TestRunParams:
    def test_defaults(self) -> None:
        p = ar.RunParams(ctx=NormalizedTurnInput(body="x"), workspace_dir="/tmp")
        assert p.tools == []
        assert p.model is None
        assert p.timeout_ms == 120_000
        assert p.light_context is False
        assert p.on_block_reply is None
        assert p.on_chunk is None

    def test_callbacks_excluded_from_serialization(self) -> None:
        async def cb(text: str) -> None:
            pass

        p = ar.RunParams(ctx=NormalizedTurnInput(body="x"), workspace_dir="/tmp", on_block_reply=cb, on_chunk=cb)
        assert "on_block_reply" not in p.model_dump()
        assert "on_chunk" not in p.model_dump()
        assert "on_block_reply" not in p.model_dump_json()
        assert "on_chunk" not in p.model_dump_json()


class TestRunLaneManager:
    def test_lock_cached_per_session_key(self) -> None:
        lm = ar.RunLaneManager()
        lock_a = lm.get_lock("a")
        lock_b = lm.get_lock("b")
        assert lm.get_lock("a") is lock_a
        assert lock_a is not lock_b

    def test_mark_active_done_lookup(self) -> None:
        lm = ar.RunLaneManager()
        assert lm.get_active_run_id("a") is None
        lm.mark_active("a", "run-1")
        assert lm.get_active_run_id("a") == "run-1"
        lm.mark_done("a")
        assert lm.get_active_run_id("a") is None
        lm.mark_done("a")  # doppeltes pop darf nicht werfen
        lm.mark_active("a", "r1")
        lm.mark_active("a", "r2")
        assert lm.get_active_run_id("a") == "r2"


# ── SessionFactory ────────────────────────────────────────────


class TestSessionFactory:
    def test_cache_hit_updates_system_prompt_and_reuses(self, tmp_path: Path) -> None:
        factory = ar.SessionFactory()
        created = FakeSession()

        async def fake_create(options: CreateSessionOptions) -> FakeSession:
            assert options.continue_session is not None
            assert options.continue_session.name == "sk.jsonl"
            assert options.continue_session.parent.name == "sessions"
            assert options.system_prompt == "PROMPT"
            assert options.cwd == str(tmp_path)
            return created

        with mock.patch.object(ar, "create_agent_session", fake_create):
            s1 = run(factory.get_or_create("sk", str(tmp_path), "PROMPT", [], None))
            s2 = run(factory.get_or_create("sk", str(tmp_path), "NEW-PROMPT", [], None))

        assert s1 is created
        assert s2 is created
        assert created.agent._options.system_prompt == "NEW-PROMPT"

    def test_explicit_model_passthrough(self, tmp_path: Path) -> None:
        factory = ar.SessionFactory()

        async def fake_create(options: CreateSessionOptions) -> FakeSession:
            assert options.model == "ollama/gpt"
            return FakeSession()

        with mock.patch.object(ar, "create_agent_session", fake_create):
            run(factory.get_or_create("k", str(tmp_path), "sp", [make_tool()], "ollama/gpt"))

    def test_model_none_becomes_empty_string(self, tmp_path: Path) -> None:
        factory = ar.SessionFactory()

        async def fake_create(options: CreateSessionOptions) -> FakeSession:
            assert options.model == ""
            return FakeSession()

        with mock.patch.object(ar, "create_agent_session", fake_create):
            run(factory.get_or_create("k", str(tmp_path), "sp", [], None))

    def test_invalidate_removes_and_is_idempotent(self, tmp_path: Path) -> None:
        factory = ar.SessionFactory()

        async def fake_create(options: CreateSessionOptions) -> FakeSession:
            return FakeSession()

        with mock.patch.object(ar, "create_agent_session", fake_create):
            run(factory.get_or_create("k", str(tmp_path), "sp", [], None))
            assert "k" in factory._cache
            factory.invalidate("k")
            assert "k" not in factory._cache
        factory.invalidate("ghost")  # KeyError-free


# ── SystemPromptBuilder ───────────────────────────────────────


class TestSystemPromptBuilder:
    @staticmethod
    def _workspace(tmp_path: Path) -> str:
        # Neu: SystemPromptBuilder.build() erwartet das Projekt-ROOT;
        # HEARTBEAT.md liegt im WORKSPACE (<root>/.selma/workspace)
        root = tmp_path / "proj"
        root.mkdir(parents=True, exist_ok=True)
        ws = root / ".selma" / "workspace"
        ws.mkdir(parents=True)
        (ws / "HEARTBEAT.md").write_text("HB-CONTENT", encoding="utf-8")
        return str(root)

    def test_light_context_includes_heartbeat(self, tmp_path: Path) -> None:
        ws = self._workspace(tmp_path)
        b = ar.SystemPromptBuilder()
        with mock.patch.object(ar, "build_system_prompt", side_effect=_base_prompt) as m:
            prompt = b.build(
                ws, simple_ctx(provider="tg", chat_type="dm", sender_name="R"), [make_tool("alpha")], light_context=True
            )
        assert m.call_count == 1
        assert prompt.startswith("BASE")
        assert "## Safety" in prompt
        assert "Channel: tg" in prompt
        assert "Chat type: dm" in prompt
        assert "Sender: R" in prompt
        assert "### HEARTBEAT.md" in prompt
        assert "HB-CONTENT" in prompt

    def test_light_context_missing_heartbeat(self, tmp_path: Path) -> None:
        ws = self._workspace(tmp_path)
        (tmp_path / "proj" / ".selma" / "workspace" / "HEARTBEAT.md").unlink()
        b = ar.SystemPromptBuilder()
        with mock.patch.object(ar, "build_system_prompt", side_effect=_base_prompt):
            prompt = b.build(ws, simple_ctx(), [], light_context=True)
        # Kein HEARTBEAT.md → kein Bootstrap-Block, nur Base + Safety + Runtime
        assert "### HEARTBEAT.md" not in prompt
        assert "HB-CONTENT" not in prompt
        assert "## Runtime" in prompt
        assert "Channel: unknown" in prompt

    def test_light_context_empty_files_no_crash(self, tmp_path: Path) -> None:
        ws = self._workspace(tmp_path)
        (tmp_path / "proj" / ".selma" / "workspace" / "HEARTBEAT.md").write_text("")
        b = ar.SystemPromptBuilder()
        with mock.patch.object(ar, "build_system_prompt", side_effect=_base_prompt):
            prompt = b.build(ws, simple_ctx(), [], light_context=True)
        assert "## Runtime" in prompt

    def test_render_context_files_empty_list(self) -> None:
        assert ar.SystemPromptBuilder()._render_context_files([]) == ""

    def test_render_context_files_truncates(self, caplog: pytest.LogCaptureFixture) -> None:
        b = ar.SystemPromptBuilder()
        f = cast(ContextFile, types.SimpleNamespace(path="/somewhere/FUZZ.md", content="Y" * 25_000))
        with caplog.at_level(logging.WARNING, logger="selma.agent_runtime"):
            out = b._render_context_files([f])
        assert out.startswith("\n\n### FUZZ.md\n")
        assert "\n[... truncated]" in out
        assert any("truncated" in r.message for r in caplog.records)

    def test_full_context_uses_resource_loader(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        ws = self._workspace(tmp_path)
        b = ar.SystemPromptBuilder()

        fake: list[ContextFile] = [
            cast(ContextFile, types.SimpleNamespace(path="/proj/AGENTS.md", content="AGENTS-CONTENT")),
            cast(ContextFile, types.SimpleNamespace(path="/proj/SOUL.md", content="SOUL-CONTENT")),
        ]

        class FakeLoader:
            def __init__(self, **kw: Any) -> None:
                pass

            def load_context_files(self) -> list[ContextFile]:
                return fake

        monkeypatch.setattr(ar, "ResourceLoader", FakeLoader)
        with (
            mock.patch.object(ar, "build_system_prompt", side_effect=_base_prompt) as m,
            caplog.at_level(logging.DEBUG, logger="selma.agent_runtime"),
        ):
            prompt = b.build(ws, simple_ctx(), [make_tool()], light_context=False)
        assert m.call_count == 1
        assert "### AGENTS.md" in prompt
        assert "AGENTS-CONTENT" in prompt
        assert "### SOUL.md" in prompt
        assert "SOUL-CONTENT" in prompt
        # build_system_prompt() wird positionell mit BuildSystemPromptOptions aufgerufen
        # Neu: cwd = Projekt-Root (workspace_dir), nicht mehr .../.selma/workspace
        assert m.call_args.args[0].cwd == str(tmp_path / "proj")
        assert "## Safety" in prompt
        assert "## Runtime" in prompt


# ── EventSubscriber ───────────────────────────────────────────


class TestEventSubscriber:
    def test_message_update_spawns_chunk(self) -> None:
        coros, fake_spawn = _captured_spawn()
        chunks: list[str] = []

        async def on_chunk(t: str) -> None:
            chunks.append(t)

        sub = ar.EventSubscriber(on_chunk=on_chunk)
        with mock.patch.object(ar, "spawn_background_task", fake_spawn):
            sub.get_listener()(AgentEvent(type="message_update", payload="hallo"))
        assert len(coros) == 1
        run(coros[0])
        assert chunks == ["hallo"]

    def test_message_update_noop_without_payload_or_cb(self) -> None:
        coros, fake_spawn = _captured_spawn()

        async def on_chunk(t: str) -> None:
            pass

        with mock.patch.object(ar, "spawn_background_task", fake_spawn):
            # Kein Payload → kein Spawn
            ar.EventSubscriber(on_chunk=on_chunk).get_listener()(AgentEvent(type="message_update", payload=None))
            # Kein Callback → kein Spawn
            ar.EventSubscriber().get_listener()(AgentEvent(type="message_update", payload="x"))
        assert coros == []

    def test_message_end_sets_final_reply(self) -> None:
        coros, fake_spawn = _captured_spawn()
        sent: list[str] = []

        async def on_block(t: str) -> None:
            sent.append(t)

        sub = ar.EventSubscriber(on_block_reply=on_block)
        with mock.patch.object(ar, "spawn_background_task", fake_spawn):
            sub.get_listener()(AgentEvent(type="message_end", payload=types.SimpleNamespace(content="Ende!")))
        assert sub.final_reply == "Ende!"
        assert len(coros) == 1
        run(coros[0])
        assert sent == ["Ende!"]

    def test_message_end_empty_or_none_noop(self) -> None:
        coros, fake_spawn = _captured_spawn()
        sub = ar.EventSubscriber()
        with mock.patch.object(ar, "spawn_background_task", fake_spawn):
            listener = sub.get_listener()
            listener(AgentEvent(type="message_end", payload=None))
            listener(AgentEvent(type="message_end", payload=types.SimpleNamespace(content="")))
        assert coros == []
        assert sub.final_reply == ""

    def test_message_end_no_block_reply_cb(self) -> None:
        coros, fake_spawn = _captured_spawn()
        sub = ar.EventSubscriber()
        with mock.patch.object(ar, "spawn_background_task", fake_spawn):
            sub.get_listener()(AgentEvent(type="message_end", payload=types.SimpleNamespace(content="X")))
        assert sub.final_reply == "X"
        assert coros == []

    def test_tool_events_log_only(self, caplog: pytest.LogCaptureFixture) -> None:
        coros, fake_spawn = _captured_spawn()
        sub = ar.EventSubscriber()
        with (
            mock.patch.object(ar, "spawn_background_task", fake_spawn),
            caplog.at_level(logging.INFO, logger="selma.agent_runtime"),
        ):
            listener = sub.get_listener()
            listener(AgentEvent(type="tool_start", payload=types.SimpleNamespace(name="ls")))
            listener(AgentEvent(type="tool_end", payload=types.SimpleNamespace(name="ls")))
            listener(AgentEvent(type="tool_start", payload=None))
            listener(AgentEvent(type="tool_end", payload=None))
        assert coros == []
        assert any("Tool start" in r.message for r in caplog.records)
        assert any("Tool end" in r.message for r in caplog.records)

    def test_agent_end_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        sub = ar.EventSubscriber()
        sub.final_reply = "abcde"
        with (
            mock.patch.object(ar, "spawn_background_task", _captured_spawn()[1]),
            caplog.at_level(logging.INFO, logger="selma.agent_runtime"),
        ):
            sub.get_listener()(AgentEvent(type="agent_end", payload=None))
        assert any("Agent end" in r.message and "5" in r.message for r in caplog.records)

    def test_unknown_event_ignored(self) -> None:
        coros, fake_spawn = _captured_spawn()
        with mock.patch.object(ar, "spawn_background_task", fake_spawn):
            ar.EventSubscriber().get_listener()(AgentEvent(type="completely_unknown", payload="?"))
        assert coros == []


# ── RunOrchestrator ───────────────────────────────────────────


class TestRunOrchestrator:
    @staticmethod
    def make_orch(session: FakeSession | None = None) -> tuple[ar.RunOrchestrator, FakeSession]:
        sess = session or FakeSession()
        orch = ar.RunOrchestrator()
        orch._sessions = cast(ar.SessionFactory, CacheFactory(sess))
        orch._prompt_builder = cast(ar.SystemPromptBuilder, types.SimpleNamespace(build=_build_returning("SYSPROMPT")))
        return orch, sess

    @staticmethod
    def params(session_key: str | None = "sk-1", **kw: Any) -> ar.RunParams:
        return ar.RunParams(
            ctx=NormalizedTurnInput(body="x", body_for_agent="Hallo", session_key=session_key),
            workspace_dir="/tmp/ws",
            **kw,
        )

    def test_successful_run(self) -> None:
        # FakeSession liefert das message_end-Event während prompt(),
        # exakt wie die echte Agent-Session (Orchestrator liest final_reply
        # erst, wenn prompt() zurückkehrt).
        sess = FakeSession(final_reply="Antwort!")
        orch, sess = self.make_orch(sess)

        with mock.patch.object(ar, "spawn_background_task", _captured_spawn()[1]):
            result = run(orch.run(self.params()))

        assert result.status == "ok"
        assert result.reply == "Antwort!"
        assert result.session_key == "sk-1"
        assert len(result.run_id) == 8
        assert result.error is None
        assert sess.prompt_calls == ["Hallo"]
        assert sess.unsubscribed == 1
        assert orch._lanes.get_active_run_id("sk-1") is None

    def test_default_session_key(self) -> None:
        orch, _ = self.make_orch()
        with mock.patch.object(ar, "spawn_background_task", _captured_spawn()[1]):
            result = run(orch.run(self.params(session_key=None)))
        assert result.session_key == "default"
        assert orch._lanes.get_active_run_id("default") is None

    def test_empty_final_reply_becomes_none(self) -> None:
        orch, _ = self.make_orch()
        with mock.patch.object(ar, "spawn_background_task", _captured_spawn()[1]):
            result = run(orch.run(self.params()))
        assert result.status == "ok"
        assert result.reply is None  # subscriber.final_reply == "" → None

    def test_cache_hit_updates_system_prompt(self) -> None:
        orch, sess = self.make_orch()
        with mock.patch.object(ar, "spawn_background_task", _captured_spawn()[1]):
            r1 = run(orch.run(self.params(session_key="k")))
            r2 = run(orch.run(self.params(session_key="k")))
        assert r1.status == "ok"
        assert r2.status == "ok"
        # Zweiter Aufruf: Cache-Hit → Prompt wird aktualisiert
        assert sess.agent._options.system_prompt == "SYSPROMPT"
        assert len(cast(CacheFactory, orch._sessions).calls) == 2

    def test_timeout(self, caplog: pytest.LogCaptureFixture) -> None:
        sess = FakeSession(prompt_delay=10_000)  # 10s Delay > Timeout
        orch, _ = self.make_orch(sess)
        caplog.at_level(logging.WARNING, logger="selma.agent_runtime")
        with mock.patch.object(ar, "spawn_background_task", _captured_spawn()[1]):
            result = run(orch.run(self.params(timeout_ms=10)))
        assert result.status == "timeout"
        assert result.error is not None
        assert "Timeout after 10ms" in result.error
        assert result.reply is None
        assert sess.unsubscribed == 1
        assert any("timeout" in r.message.lower() for r in caplog.records)
        assert orch._lanes.get_active_run_id("sk-1") is None

    def test_exception(self, caplog: pytest.LogCaptureFixture) -> None:
        class Boom(Exception):
            pass

        sess = FakeSession(raise_exc=Boom("kaputt"))
        orch, _ = self.make_orch(sess)
        caplog.at_level(logging.ERROR, logger="selma.agent_runtime")
        with mock.patch.object(ar, "spawn_background_task", _captured_spawn()[1]):
            result = run(orch.run(self.params()))
        assert result.status == "error"
        assert result.error == "kaputt"
        assert result.reply is None
        assert sess.unsubscribed == 1
        assert any("kaputt" in r.message for r in caplog.records)
        assert orch._lanes.get_active_run_id("sk-1") is None

    async def _traced_session(self) -> tuple[ar.RunOrchestrator, FakeSession]:
        """Session, die prompt start/ende in timeline einträgt."""
        sess = FakeSession(prompt_delay=0.1)
        orch, _ = self.make_orch(sess)
        return orch, sess

    def test_concurrent_runs_same_session_serialize(self) -> None:
        orch, sess = self.make_orch()
        calls: list[tuple[str, float]] = []

        async def traced_prompt(text: str) -> None:
            t = asyncio.get_running_loop().time()
            calls.append(("start", t))
            await asyncio.sleep(0.1)
            calls.append(("end", asyncio.get_running_loop().time()))

        sess.prompt = traced_prompt

        async def scenario() -> tuple[ar.RunResult, ar.RunResult]:
            pa = self.params(session_key="k")
            pb = self.params(session_key="k")
            return await asyncio.gather(orch.run(pa), orch.run(pb))

        with mock.patch.object(ar, "spawn_background_task", _captured_spawn()[1]):
            run(scenario())

        # serialisiert: 2 Starts, 2 Ends, Reihenfolge: S,E,S,E
        assert [c[0] for c in calls] == ["start", "end", "start", "end"]

    def test_different_sessions_run_concurrently(self) -> None:
        """Unterschiedliche Session-Keys dürfen parallel laufen."""
        a, b = FakeSession(prompt_delay=0.1), FakeSession(prompt_delay=0.1)
        orch = ar.RunOrchestrator()

        async def fake_goc(session_key: str, **kw: Any) -> FakeSession:
            return a if session_key == "A" else b

        orch._sessions = cast(ar.SessionFactory, types.SimpleNamespace(get_or_create=fake_goc))
        orch._prompt_builder = cast(ar.SystemPromptBuilder, types.SimpleNamespace(build=_build_returning("P")))

        async def scenario() -> tuple[ar.RunResult, ar.RunResult]:
            ra, rb = await asyncio.gather(
                orch.run(self.params(session_key="A")),
                orch.run(self.params(session_key="B")),
            )
            return ra, rb

        with mock.patch.object(ar, "spawn_background_task", _captured_spawn()[1]):
            ra, rb = run(scenario())
        assert ra.status == "ok"
        assert rb.status == "ok"
        # Beide prompted, parallele Ausführung (kein Lock auf fremde Lane)
        assert a.prompt_calls
        assert b.prompt_calls


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
