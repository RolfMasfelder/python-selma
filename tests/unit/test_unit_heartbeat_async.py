# ============================================================
# test_unit_heartbeat_async.py
#
# Coverage for the async parts of heartbeat.py that
# test_unit_heartbeat.py (pure sync functions) doesn't reach:
#   - run_heartbeat_turn (chunk collection, session keys, runtime)
#   - heartbeat_loop (disabled, alerts, ack-silencing,
#     active hours, empty HEARTBEAT.md, turn failures, target=none)
#   - small uncovered edge cases (parse, _strip_token_at_edges)
#
# Style: like test_unit_gateway.py — sync tests wrapping
# coroutines in a fresh event loop (see run() helper).
# No sleep patching (would corrupt asyncio.wait_for internals);
# loop interval is "1s" real time. Timeouts generously
# oversized (slow machine!). No LLM, no network.
# ============================================================

import asyncio
import contextlib
import sys
import types
from unittest import mock

import pytest

import selma.heartbeat as hb
from selma.config import HeartbeatConfig

# ── helpers ──────────────────────────────────────────────────


def run(coro):
    """Laueft eine Coroutine auf einer frischen Event-Loop aus."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def make_cfg(
    *,
    every: str = "1s",
    target: str = "none",
    isolated_session: bool = False,
    ack_max_chars: int = 300,
    active_hours=None,
):
    return types.SimpleNamespace(
        heartbeat=HeartbeatConfig(
            every=every,
            target=target,
            isolated_session=isolated_session,
            ack_max_chars=ack_max_chars,
            active_hours=active_hours,
        )
    )


def _runtime_env_factory(**kwargs):
    """Sync wie das echte RuntimeEnv (Pydantic-Modell)."""
    return types.SimpleNamespace(**kwargs)


class FakeRuntime(types.ModuleType):
    """Stands in for selma.runtime inside run_heartbeat_turn's imports."""

    def __init__(self, delivery_cls, run_agent):
        super().__init__("selma.runtime")
        self.DeliveryContext = delivery_cls
        self.RuntimeEnv = _runtime_env_factory
        self.agent_command = run_agent


@contextlib.contextmanager
def with_fake_runtime(delivery_cls, run_agent):
    """selma.runtime in sys.modules tauschen, danach wiederherstellen."""
    fake = FakeRuntime(delivery_cls, run_agent)
    real = sys.modules.get("selma.runtime")
    with mock.patch.dict(sys.modules, {"selma.runtime": fake}):
        try:
            yield fake
        finally:
            if real is not None:
                sys.modules["selma.runtime"] = real
            else:
                sys.modules.pop("selma.runtime", None)


def make_delivery(captured=None):
    """DC: sammelt on_partial_reply-Chunks in captured.setdefault('chunks', [])."""

    class DC:
        def __init__(self, on_partial_reply=None):
            self.on_partial_reply = on_partial_reply
            if captured is not None:
                captured.setdefault("dc_self", self)

    return DC


def _seed_heartbeat_md(tmp_path, body="- check mail\n"):
    """HEARTBEAT.md lebt im WORKSPACE (<root>/.selma/workspace); tmp_path ist Root."""
    ws = tmp_path / ".selma" / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "HEARTBEAT.md").write_text(body, encoding="utf-8")


# ── uncovered small edge cases ───────────────────────────────


class TestUncoveredPureEdges:
    def test_parse_invalid_suffixed_value_returns_zero(self):
        # Suffix erkannt, int() fällt durch → catch → warn → 0
        assert hb.parse_interval_seconds("xm") == 0

    def test_parse_no_suffix_returns_zero(self):
        assert hb.parse_interval_seconds("10") == 0

    def test_strip_token_at_edges_no_token(self):
        text, did = hb._strip_token_at_edges("hello world")
        assert text == "hello world"
        assert did is False

    def test_strip_token_at_edges_repeated_token(self):
        text, did = hb._strip_token_at_edges("HEARTBEAT_OK HEARTBEAT_OK")
        assert text == ""
        assert did is True

    def test_strip_heartbeat_token_repeated_token_is_ack(self):
        r = hb.strip_heartbeat_token("HEARTBEAT_OK HEARTBEAT_OK", mode="heartbeat")
        assert r["should_skip"] is True
        assert r["did_strip"] is True


# ── run_heartbeat_turn ───────────────────────────────────────


class TestRunHeartbeatTurn:
    def test_collects_chunks_into_reply(self, tmp_path):

        async def fake_run_agent(prompt, *, session_key=None, delivery=None, runtime=None):
            delivery.on_partial_reply("Hallo")
            delivery.on_partial_reply(" Welt")
            return None

        async def scenario():
            with with_fake_runtime(make_delivery(), fake_run_agent):
                return await hb.run_heartbeat_turn(make_cfg(), str(tmp_path))

        reply = run(scenario())
        assert reply == "Hallo Welt"

    def test_empty_reply_is_none(self, tmp_path):
        async def fake_run_agent(prompt, *, session_key=None, delivery=None, runtime=None):
            return None  # keine Chunks

        async def scenario():
            with with_fake_runtime(make_delivery(), fake_run_agent):
                return await hb.run_heartbeat_turn(make_cfg(), str(tmp_path))

        assert run(scenario()) is None

    def test_isolated_session_key_unique_and_runtime_cwd(self, tmp_path):
        captured = {}

        async def fake_run_agent(prompt, *, session_key=None, delivery=None, runtime=None):
            captured["prompt"] = prompt
            captured["session_key"] = session_key
            captured["runtime"] = runtime
            delivery.on_partial_reply("OK")
            return None

        async def scenario():
            with with_fake_runtime(make_delivery(), fake_run_agent):
                return await hb.run_heartbeat_turn(make_cfg(isolated_session=True), str(tmp_path))

        run(scenario())
        assert captured["prompt"] == hb.HEARTBEAT_PROMPT
        assert captured["session_key"].startswith("heartbeat:")
        assert captured["session_key"] != "heartbeat:main"
        assert captured["runtime"].cwd == str(tmp_path)

    def test_shared_session_key_constant(self, tmp_path):
        captured = {}

        async def fake_run_agent(prompt, *, session_key=None, delivery=None, runtime=None):
            captured["session_key"] = session_key
            delivery.on_partial_reply("OK")
            return None

        async def scenario():
            with with_fake_runtime(make_delivery(), fake_run_agent):
                return await hb.run_heartbeat_turn(make_cfg(isolated_session=False), str(tmp_path))

        run(scenario())
        assert captured["session_key"] == "heartbeat:main"


# ── heartbeat_loop ───────────────────────────────────────────
# Zeitplan der Loop: sleep(interval) → checks → turn → (push?) → …
# interval "1s" real time; Alerts landen nach 1–2 Iterationen.


async def _run_loop_until(config, ws, timeout_s, then_check_queue):
    """Startet heartbeat_loop, gibt die Queue-Zustandsprüfung ab.
    then_check_queue: 'alert' → wartet auf q.get(); 'empty' → wartet
    timeout_s und gibt die Queue zurück (muss leer sein)."""
    q: asyncio.Queue = asyncio.Queue()
    task = asyncio.ensure_future(hb.heartbeat_loop(config, str(ws), q))
    try:
        if then_check_queue == "alert":
            return await asyncio.wait_for(q.get(), timeout=timeout_s)
        return await asyncio.wait_for(q.get(), timeout=timeout_s)
    except TimeoutError:
        return q  # leer geblieben
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class TestHeartbeatLoop:
    def test_disabled_returns_immediately(self, tmp_path):
        q: asyncio.Queue = asyncio.Queue()
        (tmp_path / "HEARTBEAT.md").write_text("- mail\n", encoding="utf-8")

        async def scenario():
            await hb.heartbeat_loop(make_cfg(every="0m"), str(tmp_path), q)

        run(scenario())
        assert q.empty()

    def test_delivers_alert_to_queue(self, tmp_path, monkeypatch):
        _seed_heartbeat_md(tmp_path)

        async def fake_turn(config, workspace_dir):
            return "Server down – 2 instances offline"

        monkeypatch.setattr(hb, "run_heartbeat_turn", fake_turn)

        async def scenario():
            q: asyncio.Queue = asyncio.Queue()
            task = asyncio.ensure_future(hb.heartbeat_loop(make_cfg(every="1s", target="last"), str(tmp_path), q))
            try:
                return await asyncio.wait_for(q.get(), timeout=60)
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        alert = run(scenario())
        assert alert == "Server down – 2 instances offline"

    def test_silences_ack_replies(self, tmp_path, monkeypatch):
        """Kurz-Reply mit OK-Token → wird verschluckt, Queue bleibt leer."""
        _seed_heartbeat_md(tmp_path)

        async def fake_turn(config, workspace_dir):
            return "All good, HEARTBEAT_OK"

        monkeypatch.setattr(hb, "run_heartbeat_turn", fake_turn)

        async def scenario():
            q: asyncio.Queue = asyncio.Queue()
            task = asyncio.ensure_future(hb.heartbeat_loop(make_cfg(every="1s", target="last"), str(tmp_path), q))
            try:
                with pytest.raises((asyncio.TimeoutError, TimeoutError)):
                    await asyncio.wait_for(q.get(), timeout=10)
                return q
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        q = run(scenario())
        assert q.empty()

    def test_skips_outside_active_hours(self, tmp_path, monkeypatch, caplog):
        _seed_heartbeat_md(tmp_path)
        calls = []

        async def fake_turn(config, workspace_dir):
            calls.append(True)
            return "ALERT should never be produced"

        monkeypatch.setattr(hb, "run_heartbeat_turn", fake_turn)
        monkeypatch.setattr(hb, "is_within_active_hours", lambda cfg: False)

        async def scenario():
            q: asyncio.Queue = asyncio.Queue()
            task = asyncio.ensure_future(
                hb.heartbeat_loop(
                    make_cfg(
                        every="1s",
                        target="last",
                        active_hours={
                            "start": "09:00",
                            "end": "17:00",
                            "timezone": "UTC",
                        },
                    ),
                    str(tmp_path),
                    q,
                )
            )
            try:
                with pytest.raises((asyncio.TimeoutError, TimeoutError)):
                    await asyncio.wait_for(q.get(), timeout=10)
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        run(scenario())
        assert calls == []  # Agent-Turn wurde nie erreicht

    def test_skips_when_heartbeat_md_empty(self, tmp_path, monkeypatch):
        _seed_heartbeat_md(tmp_path, "# Leave empty to skip heartbeat calls.\n")
        calls = []

        async def fake_turn(config, workspace_dir):
            calls.append(True)
            return "ALERT"

        monkeypatch.setattr(hb, "run_heartbeat_turn", fake_turn)

        async def scenario():
            q: asyncio.Queue = asyncio.Queue()
            task = asyncio.ensure_future(hb.heartbeat_loop(make_cfg(every="1s", target="last"), str(tmp_path), q))
            try:
                with pytest.raises((asyncio.TimeoutError, TimeoutError)):
                    await asyncio.wait_for(q.get(), timeout=10)
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        run(scenario())
        assert calls == []  # leeres HEARTBEAT.md → Turn übersprungen

    def test_survives_turn_exception_and_delivers_later(self, tmp_path, monkeypatch, caplog):
        """Fehlgeschlagener Turn: ERROR-Log, Loop läuft weiter,
        ein späterer Alert kommt trotzdem an."""
        _seed_heartbeat_md(tmp_path)
        seq = [RuntimeError("boom"), "HEARTBEAT_OK", "real alert text"]

        async def fake_turn(config, workspace_dir):
            item = seq.pop(0) if seq else "HEARTBEAT_OK"
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr(hb, "run_heartbeat_turn", fake_turn)

        async def scenario():
            q: asyncio.Queue = asyncio.Queue()
            task = asyncio.ensure_future(hb.heartbeat_loop(make_cfg(every="1s", target="last"), str(tmp_path), q))
            try:
                return await asyncio.wait_for(q.get(), timeout=60)
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        with caplog.at_level("ERROR", logger="selma.heartbeat"):
            alert = run(scenario())

        assert alert == "real alert text"
        assert any("boom" in r.message for r in caplog.records)

    def test_alert_discarded_when_target_none(self, tmp_path, monkeypatch):
        _seed_heartbeat_md(tmp_path, "- check mail\n")

        async def fake_turn(config, workspace_dir):
            return "ALERT text that should not be delivered"

        monkeypatch.setattr(hb, "run_heartbeat_turn", fake_turn)

        async def scenario():
            q: asyncio.Queue = asyncio.Queue()
            task = asyncio.ensure_future(hb.heartbeat_loop(make_cfg(every="1s", target="none"), str(tmp_path), q))
            try:
                with pytest.raises((asyncio.TimeoutError, TimeoutError)):
                    await asyncio.wait_for(q.get(), timeout=10)
                return q
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        q = run(scenario())
        assert q.empty()  # target=none → nichts wird ausgeliefert

    def test_continues_after_none_reply(self, tmp_path, monkeypatch):
        """Turn liefert None → wird übersprungen, Loop weiter;
        nächster Reply kommt durch."""
        _seed_heartbeat_md(tmp_path)
        seq = [None, "after none came this"]

        async def fake_turn(config, workspace_dir):
            return seq.pop(0) if seq else "HEARTBEAT_OK"

        monkeypatch.setattr(hb, "run_heartbeat_turn", fake_turn)

        async def scenario():
            q: asyncio.Queue = asyncio.Queue()
            task = asyncio.ensure_future(hb.heartbeat_loop(make_cfg(every="1s", target="last"), str(tmp_path), q))
            try:
                return await asyncio.wait_for(q.get(), timeout=60)
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        alert = run(scenario())
        assert alert == "after none came this"
