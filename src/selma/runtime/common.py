# ============================================================
# runtime/common.py
#
# Layer 0 – geteilte Datenmodelle, Blocker sowie Lifecycle-Bus
# für das selma.runtime-Paket.
#
# Keine selma.runtime-Interna: dieses Modul hängt von keinem
# anderen selma.runtime-Submodul ab (kein Zirkel).
# ============================================================

import logging
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from selma.tracing import trace_and_log

logger = logging.getLogger("selma.runtime")

__all__ = [
    "AgentCommandResult",
    "AgentRunMeta",
    "BlockReplyChunkingConfig",
    "DeliveryContext",
    "LifecyclePhase",
    "RunPayload",
    "RuntimeEnv",
    "_BlockChunker",
    "_LIFECYCLE_LISTENERS",
    "emit_lifecycle_event",
    "on_lifecycle_event",
]


class BlockReplyChunkingConfig(BaseModel):
    """
    Configures how the streaming text is split into blocks for on_block_reply.

    A block is emitted when the buffer contains at least min_chars AND
    ends with one of the flush_patterns (checked right-to-left).
    """

    min_chars: int = 80
    flush_patterns: list[str] = Field(default_factory=lambda: ["\n\n", ".\n", "!\n", "?\n"])


class DeliveryContext(BaseModel):
    """
    All delivery-related parameters bundled into one object.
    Passed as a single field through the call chain instead of
    individual callback parameters.
    """

    # True → send response to reply_channel instead of stdout
    deliver: bool = False
    # Target channel, e.g. "telegram", "slack"
    reply_channel: str | None = None

    # Called for every incoming token chunk (real-time streaming)
    on_partial_reply: Callable[[str], None] | None = None
    # Called when a complete block (sentence / paragraph) is ready
    on_block_reply: Callable[[str], None] | None = None
    # Called once after the last block has been emitted
    on_block_reply_flush: Callable[[], None] | None = None
    # Controls how the text stream is split into blocks
    block_reply_chunking: BlockReplyChunkingConfig | None = None
    # Called when the model invokes a tool: (tool_name, arguments)
    on_tool_call: Callable[[str, dict[Any, Any]], None] | None = None

    model_config = {"arbitrary_types_allowed": True}


class RuntimeEnv(BaseModel):
    """
    Runtime context passed through all layers.
    Corresponds to RuntimeEnv in OpenClaw.
    """

    cwd: str = "."

    model_config = {"arbitrary_types_allowed": True}


class RunPayload(BaseModel):
    """
    A single response block from the agent.
    Corresponds to EmbeddedRunPayload in OpenClaw.
    """

    text: str
    is_error: bool = False


class AgentRunMeta(BaseModel):
    """
    Metadata about a completed run.
    Corresponds to EmbeddedPiAgentMeta in OpenClaw.
    """

    session_id: str
    provider: str
    model: str
    duration_ms: int
    aborted: bool = False
    stop_reason: str | None = None


class AgentCommandResult(BaseModel):
    """
    The complete result of an agentCommand call.
    Returned by run_embedded_pi_agent.
    Corresponds to EmbeddedPiRunResult in OpenClaw.
    """

    payloads: list[RunPayload] = Field(default_factory=list[RunPayload])
    meta: AgentRunMeta


class LifecyclePhase(BaseModel):
    """
    A lifecycle event for the running agent.
    Corresponds to the agent-events system in OpenClaw.
    """

    run_id: str
    phase: Literal["start", "end", "error"]
    started_at: int  # Unix-ms
    ended_at: int | None = None
    aborted: bool = False
    stop_reason: str | None = None
    error: str | None = None


_LIFECYCLE_LISTENERS: set[Callable[[LifecyclePhase], None]] = set()


def on_lifecycle_event(
    listener: Callable[[LifecyclePhase], None],
) -> Callable[[], None]:
    """
    Registers a listener for lifecycle events.
    Returns an unsubscribe callable.

    Corresponds to onSessionLifecycleEvent() in OpenClaw
    (src/sessions/session-lifecycle-events.ts).
    """
    _LIFECYCLE_LISTENERS.add(listener)
    return lambda: _LIFECYCLE_LISTENERS.discard(listener)


def emit_lifecycle_event(event: LifecyclePhase) -> None:
    """
    Broadcasts a lifecycle event to all registered listeners.
    Listener errors are caught individually so one bad listener
    cannot block the others.

    Corresponds to emitAgentEvent() in OpenClaw (src/infra/agent-events.ts).
    """
    trace_and_log(
        logger,
        f"Lifecycle | run_id={event.run_id} phase={event.phase}",
    )
    for listener in _LIFECYCLE_LISTENERS:
        try:
            listener(event)
        except Exception:
            logger.exception("lifecycle listener error")


class _BlockChunker:
    """
    Buffers streaming chunks and emits blocks at natural boundaries.

    A block is flushed when the buffer reaches min_chars AND a flush
    pattern (e.g. paragraph break, sentence-ending newline) is found.
    Any remaining text is flushed explicitly via flush().
    """

    def __init__(
        self,
        config: BlockReplyChunkingConfig,
        on_block: Callable[[str], None],
    ) -> None:
        self._patterns = config.flush_patterns
        self._min_chars = config.min_chars
        self._on_block = on_block
        self._buffer = ""

    def feed(self, chunk: str) -> None:
        self._buffer += chunk
        if len(self._buffer) < self._min_chars:
            return
        for pattern in self._patterns:
            idx = self._buffer.rfind(pattern)
            if idx >= 0:
                cut = idx + len(pattern)
                block, self._buffer = self._buffer[:cut], self._buffer[cut:]
                self._on_block(block)
                return

    def flush(self) -> None:
        if self._buffer.strip():
            self._on_block(self._buffer)
            self._buffer = ""
