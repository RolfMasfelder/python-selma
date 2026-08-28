# ============================================================
# test_unit_adapter_webchat.py
#
# Unit tests für selma/adapter_webchat.py (WebChatChannel).
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import asyncio
import sys
import types

from selma.adapter_webchat import WebChatChannel
from selma.runtime import DeliveryContext

# ── normalize ────────────────────────────────────────────


def test_normalize_defaults():
    nti = WebChatChannel.normalize({})
    assert nti.id == "anonymous"
    assert nti.body == ""
    assert nti.body_for_agent == "[Web:Web User]: "
    assert nti.body_for_commands == ""
    assert nti.session_key == "webchat:anonymous"
    assert nti.timestamp is not None
    assert nti.raw == {}


def test_normalize_explicit_values():
    raw = {"user_id": "u42", "text": "  hey  ", "user_name": "Rolf"}
    nti = WebChatChannel.normalize(raw)
    assert nti.id == "u42"
    assert nti.body == "  hey  "
    assert nti.body_for_agent == "[Web:Rolf]:   hey  "
    assert nti.body_for_commands == "hey"  # gestrippt
    assert nti.session_key == "webchat:u42"
    assert nti.raw is raw


# ── deliver ──────────────────────────────────────────────


def test_deliver_pushes_chunks_into_queue():
    queue: asyncio.Queue = asyncio.Queue()
    ctx = WebChatChannel.deliver(queue)
    assert isinstance(ctx, DeliveryContext)

    ctx.on_partial_reply("hallo")
    ctx.on_tool_call("exec", {"command": "ls"})

    assert not queue.empty()
    assert queue.get_nowait() == "hallo"
    assert queue.get_nowait() == {"type": "tool", "name": "exec"}
    assert queue.empty()


# ── is_enabled / name ────────────────────────────────────


def test_name_is_webchat():
    assert WebChatChannel.name == "webchat"


def test_is_enabled_delegates_to_config():
    class FakeConfig:
        def is_channel_enabled(self, name: str) -> bool:
            return name == "webchat"

    ch = WebChatChannel()
    assert ch.is_enabled(FakeConfig()) is True

    class OtherConfig:
        def is_channel_enabled(self, name: str) -> bool:
            return False

    assert ch.is_enabled(OtherConfig()) is False


# ── start ────────────────────────────────────────────────


def test_start_boots_uvicorn_on_configured_channel(monkeypatch):
    """start() importiert uvicorn + gateway und startet den Server.

    Mit Fake-uvicorn wird nur der Aufrufpfad verifiziert,
    ohne echten Port zu belegen.
    """
    served = {}
    serve_called = False

    class FakeServer:
        def __init__(self, config):
            self.config = config

        async def serve(self):
            nonlocal serve_called
            serve_called = True
            served["host"] = self.config.host
            served["port"] = self.config.port
            served["log_level"] = self.config.log_level

    class FakeConfig:
        def __init__(self, app, host, port, log_level):
            self.app = app
            self.host = host
            self.port = port
            self.log_level = log_level

    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.Config = FakeConfig
    fake_uvicorn.Server = FakeServer

    ch = WebChatChannel()

    class ChConfig:
        class channels:
            class webchat:
                host = "127.0.0.1"
                port = 8901
                log_level = "warning"

    loop = asyncio.new_event_loop()

    async def run():
        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
        await ch.start(ChConfig())

    loop.run_until_complete(run())
    loop.close()

    assert serve_called is True
    assert served["port"] == 8901
    assert served["host"] == "127.0.0.1"
