# ============================================================
# gateway.py unit tests
#
# Deckung für selma/gateway.py:
#   - _resolve_passthrough, _dispatch_command, _sse
#   - process_message_flow (Command-Reply, Agent, Timeout, Exception)
#   - process_message_flow_stream (chunks, error, timeout, done)
#   - Endpunkte /webchat/stream, /webchat/heartbeat/poll
#   - handle_telegram, run_gateway (registry-Logik), lifespan
#
# Wichtig: gateway.py baut bei Modul-Import config = load_config() und
# einen CommandManager. Deshalb: vor dem Import SELMA_STATE_DIR auf ein
# tmp-Verzeichnis mit gültigem selma.json setzen (heartbeat.every="0m"
# für einen schnellen Exit der lifespan-Logik).
# ============================================================

import asyncio
import contextlib
import json
import logging
from pathlib import Path

import pytest
from fastapi import HTTPException

# -- gateway Modul erst NACH Config-Setup importieren --------------
_CONFIG_DIR = Path(".selma")
if not (_CONFIG_DIR / "selma.json").exists():
    # Repo-default: Config ist vorhanden, aber sicherheitshalber prüfen
    pass  # Tests laufen aus dem Repo-Root; Config liegt in .selma/

_DEFAULT_CONFIG = {
    "agent": {"id": "main", "name": "TestSelma"},
    "model": {"model": "test/model", "timeout_seconds": 2, "thinking": "off"},
    "channels": {"webchat": {"enabled": True}, "telegram": {"enabled": False}},
    "heartbeat": {"every": "0m", "target": "none"},
}


import os  # noqa: E402
import tempfile  # noqa: E402

_tmp_state = tempfile.mkdtemp(prefix="gw_test_state_")
os.environ["SELMA_STATE_DIR"] = _tmp_state
Path(_tmp_state, "selma.json").write_text(json.dumps(_DEFAULT_CONFIG), encoding="utf-8")

import selma.gateway as gateway  # noqa: E402
from selma import task_manager  # noqa: E402
from selma.data import NormalizedTurnInput, WebChatIn  # noqa: E402
from selma.gateway import (  # noqa: E402  # noqa: E402  # noqa: E402  # noqa: E402
    _resolve_passthrough,
    _sse,
    handle_telegram,
    handle_webchat,
    poll_heartbeat_alert,
    process_message_flow,
    process_message_flow_stream,
)
from selma.runtime import DeliveryContext  # noqa: E402

# -- Helpers -------------------------------------------------------


def _ctx(body: str, commands: str | None = None, agent: str | None = None) -> NormalizedTurnInput:
    return NormalizedTurnInput(
        id="u1",
        body=body,
        body_for_agent=agent if agent is not None else f"[Web:Test]: {body}",
        body_for_commands=commands if commands is not None else body.strip(),
        session_key="webchat:u1",
    )


def _delivery() -> DeliveryContext:
    return DeliveryContext(deliver=False, reply_channel="webchat")


def run(coro):
    """Laueft eine Coroutine auf einer frischen Event-Loop aus."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ============================================================
# _sse
# ============================================================


class TestSse:
    def test_formats_data_line_with_double_newline(self):
        out = _sse({"type": "chunk", "text": "abc"})
        assert out == 'data: {"type": "chunk", "text": "abc"}\n\n'

    def test_json_escapes_non_ascii_and_quotes(self):
        out = _sse({"type": "chunk", "text": 'ä "quoted"'})
        assert out.startswith("data: ")
        payload = json.loads(out[len("data: ") :].strip())
        assert payload["text"] == 'ä "quoted"'


# ============================================================
# _resolve_passthrough
# ============================================================


class TestResolvePassthrough:
    def test_skill_with_name_only(self):
        assert _resolve_passthrough("/skill summarize") == "Run the skill summarize"

    def test_skill_with_name_and_input(self):
        assert _resolve_passthrough("/skill summarize the file") == "Run the skill summarize the file"

    def test_skill_without_name_is_not_passthrough(self):
        assert _resolve_passthrough("/skill") is None

    def test_healthcheck_strips_leading_slash(self):
        assert _resolve_passthrough("/healthcheck") == "healthcheck"

    def test_other_commands_and_plain_text_are_not_passthrough(self):
        assert _resolve_passthrough("/reset") is None
        assert _resolve_passthrough("hallo") is None
        # Note: "" wirft IndexError (unreachable in der Praxis —
        # _dispatch_command ruft es nur mit body_for_commands mit "/"-Präfix auf)


# ============================================================
# _dispatch_command
# ============================================================


class StubCommandManager:
    def __init__(self, reply: str = "cmd done"):
        self.reply = reply
        self.calls = 0

    async def handle(self, ctx, delivery=None):
        self.calls += 1
        return self.reply


class TestDispatchCommand:
    def test_plain_text_is_forwarded_to_agent(self, monkeypatch):
        stub = StubCommandManager()
        monkeypatch.setattr(gateway, "_command_manager", stub)
        ctx = _ctx("hallo welt")
        assert run(gateway._dispatch_command(ctx)) is None
        assert stub.calls == 0
        assert ctx.body_for_agent.startswith("[Web:Test]: hallo welt")

    def test_known_command_goes_to_command_manager(self, monkeypatch):
        stub = StubCommandManager(reply="Model set to `x`.")
        monkeypatch.setattr(gateway, "_command_manager", stub)
        ctx = _ctx("irgendein text", commands="/model x")
        reply = run(gateway._dispatch_command(ctx, _delivery()))
        assert reply == "Model set to `x`."
        assert stub.calls == 1

    def test_skill_passthrough_rewrites_body_for_agent_and_skips_manager(self, monkeypatch):
        stub = StubCommandManager()
        monkeypatch.setattr(gateway, "_command_manager", stub)
        ctx = _ctx("irgendwas", commands="/skill summarize my file")
        assert run(gateway._dispatch_command(ctx)) is None
        assert stub.calls == 0
        assert ctx.body_for_agent == "Run the skill summarize my file"

    def test_healthcheck_passthrough_strips_slash(self, monkeypatch):
        stub = StubCommandManager()
        monkeypatch.setattr(gateway, "_command_manager", stub)
        ctx = _ctx("x", commands="/healthcheck")
        assert run(gateway._dispatch_command(ctx)) is None
        assert stub.calls == 0
        assert ctx.body_for_agent == "healthcheck"


# ============================================================
# process_message_flow
# ============================================================


class TestProcessMessageFlow:
    def test_command_reply_is_returned_and_agent_never_runs(self, monkeypatch):
        agent_calls = []

        async def fake_agent(message, **kwargs):
            agent_calls.append(message)

        monkeypatch.setattr(gateway, "_command_manager", StubCommandManager(reply="cmd done"))
        monkeypatch.setattr(gateway, "run_agent", fake_agent)

        ctx = _ctx("irgenwas", commands="/help")
        assert run(process_message_flow(ctx, _delivery())) == "cmd done"
        assert agent_calls == []

    def test_agent_path_returns_none_and_passes_session_and_delivery(self, monkeypatch):
        seen = {}

        async def fake_agent(message, *, session_key=None, delivery=None, runtime=None):
            seen["message"] = message
            seen["session_key"] = session_key
            seen["delivery"] = delivery
            seen["runtime"] = runtime

        monkeypatch.setattr(gateway, "run_agent", fake_agent)
        delivery = _delivery()
        ctx = _ctx("mach was")

        assert run(process_message_flow(ctx, delivery)) is None
        assert seen["message"] == ctx.body_for_agent
        assert seen["session_key"] == "webchat:u1"
        assert seen["delivery"] is delivery
        assert seen["runtime"].cwd == "."

    def test_timeout_returns_retry_message(self, monkeypatch):
        async def fake_agent(message, **kwargs):
            raise TimeoutError("zu langsam")

        monkeypatch.setattr(gateway, "run_agent", fake_agent)
        reply = run(process_message_flow(_ctx("halt"), _delivery()))
        assert reply == "This is taking a bit longer — please try again."

    def test_agent_exception_returns_error_string(self, monkeypatch):
        async def fake_agent(message, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(gateway, "run_agent", fake_agent)
        reply = run(process_message_flow(_ctx("halt"), _delivery()))
        assert reply == "Error: boom"


# ============================================================
# process_message_flow_stream (SSE-Generator)
# ============================================================


def _drain_stream(ctx, monkeypatch):
    async def collect():
        events = [line async for line in process_message_flow_stream(ctx)]
        await task_manager.shutdown()
        return events

    return run(collect())


def _parse_sse(raw: str) -> dict:
    assert raw.startswith("data: ") and raw.endswith("\n\n")
    return json.loads(raw[len("data: ") :])


class TestProcessMessageFlowStream:
    def test_agent_stream_yields_tool_chunks_and_done(self, monkeypatch):
        async def fake_agent(message, *, session_key=None, delivery=None, runtime=None):
            assert delivery.on_tool_call is not None
            assert delivery.on_partial_reply is not None
            delivery.on_tool_call("read", {"path": "f.md"})
            delivery.on_partial_reply("Hallo")
            delivery.on_partial_reply("Welt")

        monkeypatch.setattr(gateway, "run_agent", fake_agent)
        events = [_parse_sse(e) for e in _drain_stream(_ctx("hi"), monkeypatch)]

        assert events[0] == {"type": "tool", "name": "read"}
        assert [e for e in events if e == {"type": "chunk", "text": "Hallo"}]
        assert [e for e in events if e == {"type": "chunk", "text": "Welt"}]
        assert events[-1] == {"type": "done", "session_key": "webchat:u1"}

    def test_short_command_reply_is_streamed_as_chunk(self, monkeypatch):
        monkeypatch.setattr(gateway, "_command_manager", StubCommandManager(reply="Model: x"))

        async def no_agent(message, **kwargs):
            raise AssertionError("agent darf bei Command nicht laufen")

        monkeypatch.setattr(gateway, "run_agent", no_agent)

        events = [_parse_sse(e) for e in _drain_stream(_ctx("x", commands="/status"), monkeypatch)]
        assert {"type": "chunk", "text": "Model: x"} in events
        assert events[-1]["type"] == "done"

    def test_stream_error_then_done(self, monkeypatch):
        # Hinweis: Agent-Exceptions fallen in process_message_flow auf
        # ("Error: …"-Chunk, siehe TestProcessMessageFlow) und emittieren
        # NICHT den Stream-Error-Event. Dieser zündet nur, wenn
        # process_message_flow selbst auswirft — z. B. per CommandManager.
        class _FailingCommandManager:
            async def handle(self, ctx, delivery=None):
                raise ValueError("kaputt")

        monkeypatch.setattr(gateway, "_command_manager", _FailingCommandManager())
        events = [_parse_sse(e) for e in _drain_stream(_ctx("x", commands="/boom"), monkeypatch)]

        assert [e for e in events if e["type"] == "error" and "kaputt" in e["message"]]
        assert events[-1] == {"type": "done", "session_key": "webchat:u1"}

    def test_idle_timeout_yields_stream_timeout(self, monkeypatch):
        async def fake_agent(message, **kwargs):
            await asyncio.sleep(30)  # länger als jede idle-Timeout

        async def fake_wait_for(fut, timeout=None):
            return fut if asyncio.isfuture(fut) else await fut

        # wait_for wird nur für queue.get() verwendet → hier erzwingen wir
        # einen Timeout, ohne 300 s zu warten.
        async def raising_wait_for(fut, timeout=None):
            raise TimeoutError

        monkeypatch.setattr(gateway, "run_agent", fake_agent)
        monkeypatch.setattr(gateway.asyncio, "wait_for", raising_wait_for)

        events = [_parse_sse(e) for e in _drain_stream(_ctx("x"), monkeypatch)]
        assert [e for e in events if e["type"] == "error" and e["message"] == "Stream timeout."]
        assert events[-1]["type"] == "done"


# ============================================================
# Endpunkte: /webchat/stream
# ============================================================


class TestHandleWebchat:
    def test_returns_sse_streaming_response_with_events(self, monkeypatch):
        async def fake_agent(message, *, session_key=None, delivery=None, runtime=None):
            delivery.on_partial_reply("Hi von API")

        monkeypatch.setattr(gateway, "run_agent", fake_agent)

        async def body():
            resp = await handle_webchat(WebChatIn(user_id="u1", text="hallo"))
            assert resp.media_type == "text/event-stream"
            assert resp.headers.get("X-Accel-Buffering") == "no"
            raw = [c async for c in resp.body_iterator]
            await task_manager.shutdown()
            return [_parse_sse(x) for x in raw]

        events = run(body())
        assert {"type": "chunk", "text": "Hi von API"} in events
        assert events[-1] == {"type": "done", "session_key": "webchat:u1"}

    def test_http_error_when_normalize_fails(self, monkeypatch):
        def boom(_cls, _raw):
            raise ValueError("normalize exploded")

        monkeypatch.setattr(gateway.WebChatChannel, "normalize", classmethod(boom))
        with pytest.raises(HTTPException) as excinfo:
            run(handle_webchat(WebChatIn(user_id="u1", text="x")))
        assert excinfo.value.status_code == 500
        assert "normalize exploded" in excinfo.value.detail


# ============================================================
# Endpunkt: /webchat/heartbeat/poll
# ============================================================


class TestPollHeartbeatAlert:
    def test_drains_queue_fifo_then_returns_none(self):
        # vorherige Alerts leeren, damit der Test deterministisch ist
        while True:
            try:
                gateway._pending_alerts.get_nowait()
            except asyncio.queues.QueueEmpty:
                break

        async def body():
            gateway._pending_alerts.put_nowait("erste alert")
            gateway._pending_alerts.put_nowait("zweite alert")
            first = await poll_heartbeat_alert()
            second = await poll_heartbeat_alert()
            empty = await poll_heartbeat_alert()
            return first, second, empty

        first, second, empty = run(body())
        assert first == {"alert": "erste alert"}
        assert second == {"alert": "zweite alert"}
        assert empty == {"alert": None}


# ============================================================
# handle_telegram
# ============================================================


def _make_update(text: str = "hi", replies: list | None = None):
    from datetime import datetime as _dt
    from types import SimpleNamespace

    replies = replies if replies is not None else []

    async def reply_text(msg_text):
        replies.append(msg_text)

    msg = SimpleNamespace(text=text, caption=None, message_id=42, date=_dt.now(), reply_text=reply_text)
    chat = SimpleNamespace(id=12345, type="private")
    user = SimpleNamespace(first_name="Rolf")
    bot = SimpleNamespace(username="selma_bot")
    update = SimpleNamespace(
        effective_message=msg,
        effective_chat=chat,
        effective_user=user,
        get_bot=lambda: bot,
        message=msg,
    )
    return update, reply_text, replies


class TestHandleTelegram:
    def test_agent_delivery_flush_sends_reply(self, monkeypatch):
        updates, _reply, replies = _make_update()

        async def fake_agent(message, *, session_key=None, delivery=None, runtime=None):
            delivery.on_partial_reply("Telegram-Antwort ")
            delivery.on_partial_reply("da.")
            delivery.on_block_reply_flush()

        monkeypatch.setattr(gateway, "run_agent", fake_agent)

        async def body():
            await handle_telegram(updates, None)
            await task_manager.shutdown()

        run(body())
        assert replies == ["Telegram-Antwort da."]

    def test_command_reply_goes_through_reply_text_directly(self, monkeypatch):
        updates, _reply, replies = _make_update(
            text="/help",
        )

        async def fake_agent(message, **kwargs):
            pytest.fail("agent darf bei Command nicht laufen")

        monkeypatch.setattr(gateway, "_command_manager", StubCommandManager(reply="Help ok"))
        monkeypatch.setattr(gateway, "run_agent", fake_agent)

        async def body():
            await handle_telegram(updates, None)
            await task_manager.shutdown()

        run(body())
        assert replies == ["Help ok"]

    def test_normalize_error_is_swallowed_and_logged(self, monkeypatch, caplog):
        import logging

        updates, _reply, replies = _make_update()

        def boom(_cls, _update):
            raise ValueError("no message in update")

        monkeypatch.setattr(gateway.TelegramChannel, "normalize", classmethod(boom))
        with caplog.at_level(logging.ERROR):
            run(handle_telegram(updates, None))  # darf nicht werfen
        assert replies == []
        assert "Telegram error" in caplog.text


# ============================================================
# Registry & run_gateway
# ============================================================


class TestRunGateway:
    def test_starts_only_enabled_channels_and_shuts_down(self, monkeypatch, tmp_path):
        started = []

        async def fake_start(config):
            started.append(config is gateway.config)
            await asyncio.sleep(0.02)

        # webchat ist in der Test-Config enabled → patchen, um keinen echten
        # uvicorn-Server zu starten; telegram ist disabled → wird übersprungen.
        monkeypatch.setattr(gateway._registry[1], "start", fake_start)
        assert gateway._registry[0].is_enabled(gateway.config) is False
        assert gateway._registry[1].is_enabled(gateway.config) is True

        prev_handlers = list(logging.getLogger().handlers)
        monkeypatch.setattr(gateway, "tracing_setup", lambda *a, **k: None)
        monkeypatch.chdir(tmp_path)

        run(gateway.run_gateway())

        assert started == [True]
        assert (tmp_path / "gateway.log").exists()
        # logging-Handler aufräumen (wurden im Test zum root-Logger hinzugefügt)
        root = logging.getLogger()
        for h in list(root.handlers):
            if h not in prev_handlers:
                root.removeHandler(h)
                h.close()

    def test_registry_contains_both_channels(self):
        assert set(type(ch) for ch in gateway._registry) == {
            gateway.TelegramChannel,
            gateway.WebChatChannel,
        }


# ============================================================
# lifespan
# ============================================================


class TestLifespan:
    def test_starts_heartbeat_then_cancel_on_exit(self, monkeypatch):
        started = 0

        async def fake_heartbeat_loop(config, cwd, queue):
            nonlocal started
            started += 1
            print(f"[test] fake_heartbeat_loop started (every={getattr(config.heartbeat, 'every', '?')})", flush=True)
            await asyncio.Event().wait()  # läuft, bis gecancelt

        monkeypatch.setattr("selma.heartbeat.heartbeat_loop", fake_heartbeat_loop)

        async def body():
            with contextlib.suppress(asyncio.CancelledError):
                async with gateway.lifespan(gateway.api):
                    pass

        run(body())
        assert started == 1
