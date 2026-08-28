# ============================================================
# test_unit_delivery.py
#
# Unit tests für selma/delivery.py (deliver_result).
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import asyncio

from selma import delivery
from selma.runtime import AgentCommandResult, AgentRunMeta, DeliveryContext, RunPayload


def _result(payloads: list[RunPayload], aborted: bool = False) -> AgentCommandResult:
    return AgentCommandResult(
        payloads=payloads,
        meta=AgentRunMeta(
            session_id="s1",
            provider="ollama",
            model="qwen3.8",
            duration_ms=5,
            aborted=aborted,
        ),
    )


def test_prints_payloads_to_stdout_when_no_streaming(capsys):
    result = _result([RunPayload(text="hallo"), RunPayload(text="welt")])
    asyncio.run(delivery.deliver_result(result, None))
    out = capsys.readouterr().out
    assert "hallo" in out and "welt" in out


def test_error_payload_goes_to_stderr_even_when_streamed(capsys):
    result = _result([RunPayload(text="boom", is_error=True)])
    ctx = DeliveryContext(on_partial_reply=lambda t: None)
    asyncio.run(delivery.deliver_result(result, ctx))
    captured = capsys.readouterr()
    assert "boom" in captured.err
    assert "boom" not in captured.out


def test_no_stdout_when_already_streamed(capsys):
    """already_streamed=True → normale Payloads werden NICHT noch mal ausgegeben."""
    seen: list[str] = []
    result = _result([RunPayload(text="chunk")])
    ctx = DeliveryContext(on_partial_reply=seen.append)
    asyncio.run(delivery.deliver_result(result, ctx))
    assert "chunk" not in capsys.readouterr().out


def test_none_delivery_treated_as_not_streamed(capsys):
    result = _result([RunPayload(text="x")])
    asyncio.run(delivery.deliver_result(result, None))
    assert "x" in capsys.readouterr().out
