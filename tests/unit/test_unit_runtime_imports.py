from __future__ import annotations

from selma import runtime
from selma.runtime import attempt, command, common


def test_runtime_facade_exports_every_declared_name() -> None:
    assert not [name for name in runtime.__all__ if not hasattr(runtime, name)]


def test_runtime_facade_reexports_layer_symbols() -> None:
    assert runtime.DeliveryContext is common.DeliveryContext
    assert runtime.agent_command is command.agent_command
    assert runtime.memory_flush is command.memory_flush
    assert runtime.run_embedded_pi_agent is attempt.run_embedded_pi_agent
    assert runtime.run_embedded_attempt is attempt.run_embedded_attempt
