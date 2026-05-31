# ============================================================
# compaction.py
#
# Compacts a session history via an LLM call.
#
# Delegates to AgentSession.compact() from my_mono.
# compact() summarises the conversation history so far,
# writes a compaction entry to the JSONL file, and
# replaces the active context with the summary.
#
# Corresponds to contextEngine.compact() in OpenClaw
# (src/agents/pi-embedded-runner/compact.ts).
# ============================================================

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel
from my_mono.agent_session import CreateSessionOptions, create_agent_session
from my_mono.tracing import trace_and_log


class CompactionResult(BaseModel):
    """
    Result of a compaction attempt.

    compact_session() returns this object so run_embedded_pi_agent
    can decide whether a retry makes sense.

    Corresponds to the return value of contextEngine.compact() in OpenClaw.
    """
    ok: bool           # True = compaction was successful
    compacted: bool    # True = something was actually compacted
    reason: str = ""   # Error description if ok=False or compacted=False
    # Optional diagnostic data
    tokens_before: int = 0
    tokens_after: int = 0

logger = logging.getLogger(__name__)


async def compact_session(
    session_file: str,
    config,
) -> CompactionResult:
    """
    Compacts the session history in session_file.

    Steps:
      1. Check if session_file exists — if not, nothing to compact
         (ok=True, compacted=False)
      2. Open AgentSession from the existing JSONL file
         (continue_session → SessionManager reads the history)
      3. Record the number of messages before compaction
      4. Call session.compact() → LLM summarises, compaction entry
         is written to JSONL, active context replaced by summary
      5. Return CompactionResult with before/after counters

    Returns CompactionResult:
      ok=True,  compacted=True  → compaction succeeded, retry makes sense
      ok=True,  compacted=False → nothing to compact (file missing
                                  or session too short)
      ok=False, compacted=False → error during compaction
    """
    path = Path(session_file)

    # ── 1. File must exist ───────────────────────────────────
    if not path.exists():
        trace_and_log(logger, f"compact_session: file does not exist | path={path}")
        return CompactionResult(
            ok=True,
            compacted=False,
            reason="Session file does not exist",
        )

    # ── 2. Open AgentSession ─────────────────────────────────
    try:
        session = await create_agent_session(
            CreateSessionOptions(
                model="",                        # read from JSONL
                ollama_base_url=config.model.ollama_base_url,
                continue_session=path,
            )
        )
    except Exception as e:
        logger.warning("compact_session: could not open session | error=%s", e)
        return CompactionResult(
            ok=False,
            compacted=False,
            reason=str(e),
        )

    # ── 3. Count messages ────────────────────────────────────
    messages_before = len(session.state.messages)

    if messages_before < 2:
        trace_and_log(logger, f"compact_session: too few messages | count={messages_before}")
        return CompactionResult(
            ok=True,
            compacted=False,
            reason="Too few messages to compact",
        )

    # Token estimate: approximately 4 characters per token
    tokens_before = sum(
        len(str(getattr(m, "content", m))) for m in session.state.messages
    ) // 4

    # ── 4. Compact ───────────────────────────────────────────
    try:
        await session.compact()
    except Exception as e:
        logger.warning("compact_session: compact() failed | error=%s", e)
        return CompactionResult(
            ok=False,
            compacted=False,
            reason=str(e),
        )

    # ── 5. Return result ─────────────────────────────────────
    messages_after = len(session.state.messages)
    tokens_after = sum(
        len(str(getattr(m, "content", m))) for m in session.state.messages
    ) // 4

    trace_and_log(logger, f"compact_session: compaction successful | messages {messages_before}→{messages_after} tokens_est {tokens_before}→{tokens_after}")

    return CompactionResult(
        ok=True,
        compacted=True,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
    )
