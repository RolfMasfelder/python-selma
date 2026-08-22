# ============================================================
# delivery.py
#
# Delivers the result of an agent run to the output channel.
#
# Currently implemented:
#   - stdout (CLI mode)
#
# Extensible later to:
#   - Webchat (via WebSocket adapter)
#   - Telegram (via Telegram adapter)
#   - Other channels
#
# Corresponds to deliverAgentCommandResult() in OpenClaw
# (src/commands/agent-command.ts).
# ============================================================

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from selma.tracing import trace_and_log

if TYPE_CHECKING:
    from selma.runtime import AgentCommandResult, DeliveryContext

logger = logging.getLogger(__name__)


async def deliver_result(
    result: AgentCommandResult,
    delivery: DeliveryContext | None,
) -> None:
    """
    Delivers the result of an agent run to the output channel.

    Currently:
      - Prints the text of all payloads to stdout.
      - Error payloads are written to stderr.

    Later:
      - delivery.deliver=True + delivery.reply_channel → call channel adapter
        (webchat, Telegram, etc.)

    Corresponds to deliverAgentCommandResult() in OpenClaw.
    """
    stream_out = sys.stdout
    stream_err = sys.stderr

    already_streamed = delivery is not None and delivery.on_partial_reply is not None

    for payload in result.payloads:
        if payload.is_error:
            print(payload.text, file=stream_err)
            logger.warning("Error payload delivered | text=%s", payload.text[:120])
        elif not already_streamed:
            print(payload.text, file=stream_out)

    trace_and_log(logger, f"deliver_result | payloads={len(result.payloads)} aborted={result.meta.aborted}")
