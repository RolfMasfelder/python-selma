# ============================================================
# runtime.py

# Simplified reimplementation of OpenClaw's three layers:
#
#   agentCommand          →  Layer 1: Who / Where / With what
#   run_embedded_pi_agent →  Layer 2: How often / Which key
#   run_embedded_attempt  →  Layer 3: What exactly
#
# ============================================================

from __future__ import annotations

import asyncio
import logging
import os
import platform
import uuid
from datetime import date
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, Field

from my_mono.agent import AgentEvent
from my_mono.tracing import add_span_infos, trace_and_log, tracer
from my_mono.agent_session import (
    AgentSession,
    CreateSessionOptions,
    SessionManager as AgentSessionManager,
    create_agent_session,
)
from resource_loader import ResourceLoader

from config import SelmaConfig, load_config, THINKING_LEVEL_ORDER
from system_prompt import (
    BootstrapMode,
    BuildAgentSystemPromptParams,
    EmbeddedContextFile,
    RuntimeInfo,
    build_agent_system_prompt,
    build_agent_user_prompt_prefix,
    build_runtime_line,
)
from session_store import (
    SessionRecord,
    SessionStore,
    SkillsSnapshot,
    load_session_store,
    resolve_session,
    save_session_store,
    resolve_session_file,
    update_session_store_after_run,
    is_session_fresh,
    reset_session,
)
from skills import get_skills_snapshot_version, build_skill_snapshot
from config import get_default_model, resolve_thinking_default, resolve_timeout, resolve_tools_allow
from delivery import deliver_result
from helper import get_workspace, now_ms, now_iso
from tools import ALL_TOOL_NAMES, create_selma_tools
from compaction import compact_session, CompactionResult

logger = logging.getLogger(__name__)

# -- Layer 1: agentCommand - Orchestration & Context ----------

# -- Layer 1 - Data structures

class BlockReplyChunkingConfig(BaseModel):
    """
    Configures how the streaming text is split into blocks for on_block_reply.

    A block is emitted when the buffer contains at least min_chars AND
    ends with one of the flush_patterns (checked right-to-left).
    """
    min_chars: int = 80
    flush_patterns: list[str] = Field(
        default_factory=lambda: ["\n\n", ".\n", "!\n", "?\n"]
    )


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
    on_tool_call: Callable[[str, dict], None] | None = None

    model_config = {"arbitrary_types_allowed": True}




class RuntimeEnv(BaseModel):
    """
    Runtime context passed through all layers.
    Corresponds to RuntimeEnv in OpenClaw.
    """
    cwd: str = "."
    agent_dir: str = ".selma"

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
    payloads: list[RunPayload] = Field(default_factory=list)
    meta: AgentRunMeta


class LifecyclePhase(BaseModel):
    """
    A lifecycle event for the running agent.
    Corresponds to the agent-events system in OpenClaw.
    """
    run_id: str
    phase: Literal["start", "end", "error"]
    started_at: int                        # Unix-ms
    ended_at: int | None = None
    aborted: bool = False
    stop_reason: str | None = None
    error: str | None = None

# -- Layer 1 - Functions -----------------------------------------

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
    trace_and_log(logger,
        f"Lifecycle | run_id={event.run_id} phase={event.phase}",
    )
    for listener in _LIFECYCLE_LISTENERS:
        try:
            listener(event)
        except Exception:
            logger.exception("lifecycle listener error")


def _resolve_skills_snapshot(
    session_record: SessionRecord,
    workspace_dir: str,
    is_new_session: bool,
) -> SkillsSnapshot:
    current_version = get_skills_snapshot_version(workspace_dir)
    needs_refresh = (
        is_new_session
        or session_record.skills_snapshot is None
        or session_record.skills_snapshot.version != current_version
    )
    if needs_refresh:
        snapshot = build_skill_snapshot(workspace_dir, current_version)
        session_record.skills_snapshot = snapshot
        trace_and_log(logger,
            f"Skills snapshot rebuilt | version={current_version} skills={snapshot.skill_names}",
        )
    else:
        snapshot = session_record.skills_snapshot
    return snapshot


def get_session(
    session_key: str | None,
    session_id: str | None,
    config: "SelmaConfig",
    cwd: str,
) -> tuple["SessionStore", "SessionRecord", bool, str]:
    """
    Loads the session store, resolves the session, resets it if stale,
    and determines the session file path.
    Returns (store, session_record, is_new_session, session_file).
    """
    store = load_session_store(cwd=cwd)
    session_record, is_new_session = resolve_session(store, session_key, session_id, config)

    if not is_new_session and not is_session_fresh(
        session_record,
        at_hour=config.session.reset.at_hour,
        idle_minutes=config.session.reset.idle_minutes,
    ):
        trace_and_log(logger, f"Session stale, resetting | key={session_record.session_key}")
        session_record = reset_session(store, session_record, cwd=cwd)
        is_new_session = True

    session_file = resolve_session_file(session_record, cwd=cwd)
    if not session_record.transcript_file:
        session_record.transcript_file = session_file

    return store, session_record, is_new_session, session_file


def detect_bootstrap_mode(workspace_dir: str) -> BootstrapMode:
    """
    Detects bootstrap mode based on BOOTSTRAP.md content:
      - BOOTSTRAP.md exists and has content → "full"
      - BOOTSTRAP.md missing or empty      → "none"

    Selma signals completion by clearing (not deleting) BOOTSTRAP.md,
    so no delete tool is needed.

    Corresponds to the bootstrap-mode detection in OpenClaw
    (src/agents/bootstrap-mode.ts).
    """
    bootstrap_file = Path(workspace_dir) / "BOOTSTRAP.md"
    if not bootstrap_file.exists():
        return "none"
    return "full" if bootstrap_file.read_text(encoding="utf-8").strip() else "none"


# -- Layer 1 - agent_command -------------------------------

@tracer.chain(name="agent_command")
async def agent_command(
    message: str,
    *,
    session_key: str | None = None,
    session_id: str | None = None,
    abort_signal: asyncio.Event | None = None,
    delivery: DeliveryContext | None = None,
    runtime: RuntimeEnv | None = None,
) -> AgentCommandResult:
    """
    Layer 1 – Orchestration and context.

    Answers the questions: Who may do what? With which model?
    In which session? Where does the response go?

    Corresponds to agentCommand() in OpenClaw
    (src/commands/agent-command.ts).
    """

    runtime = runtime or RuntimeEnv()
    delivery = delivery or DeliveryContext()

    if not message.strip():
        raise ValueError("message must not be empty")

    if not session_key and not session_id:
        raise ValueError(
            "At least session_key or session_id must be provided"
        )

    run_id = str(uuid.uuid4())[:8]
    started_at = now_ms()

    add_span_infos(run_id=run_id, session_key=session_key)

    config = load_config(runtime.cwd)

    store, session_record, is_new_session, session_file = get_session(session_key, session_id, config, runtime.cwd)

    workspace_dir = get_workspace(runtime.cwd)

    bootstrap_mode = detect_bootstrap_mode(workspace_dir) # BOOTSTRAP.md exists → "full" access, missing → "none"

    skills_snapshot = _resolve_skills_snapshot(session_record, workspace_dir, is_new_session)

    session_record.updated_at = now_iso()
    save_session_store(store)

    default_provider, default_model = get_default_model(config)

    provider = session_record.provider_override or default_provider
    model = session_record.model_override or default_model

    thinking_level = session_record.thinking_level or resolve_thinking_default(config, provider, model)

    trace_and_log(logger,
        f"Model | provider={provider} model={model} thinking={thinking_level}",
    )

    timeout_seconds = resolve_timeout(config)
    timeout_ms = timeout_seconds * 1000 if timeout_seconds > 0 else 0

    tools_allow = resolve_tools_allow(config)

    emit_lifecycle_event(LifecyclePhase(
        run_id=run_id,
        phase="start",
        started_at=started_at,
    ))

    lifecycle_ended = False
    result: AgentCommandResult

    try:
        # Layer 2: run_embedded_pi_agent handles retry/fallback
        result = await run_embedded_pi_agent(RunEmbeddedPiAgentOptions(
            prompt=message,
            session_record=session_record,
            session_file=session_file,
            workspace_dir=workspace_dir,
            provider=provider,
            model=model,
            thinking_level=thinking_level,
            timeout_ms=timeout_ms,
            run_id=run_id,
            skills_snapshot=skills_snapshot,
            config=config,
            bootstrap_mode=bootstrap_mode,
            abort_signal=abort_signal,
            delivery=delivery,
            tools_allow=tools_allow,
        ))

        stop_reason = result.meta.stop_reason
        emit_lifecycle_event(LifecyclePhase(
            run_id=run_id,
            phase="end",
            started_at=started_at,
            ended_at=now_ms(),
            aborted=result.meta.aborted,
            stop_reason=stop_reason,
        ))
        lifecycle_ended = True

    except Exception as err:
        if not lifecycle_ended:
            emit_lifecycle_event(LifecyclePhase(
                run_id=run_id,
                phase="error",
                started_at=started_at,
                ended_at=now_ms(),
                error=str(err),
            ))
        logger.exception("agentCommand failed | run_id=%s", run_id)
        raise

    await update_session_store_after_run(
        store=store,
        session_record=session_record,
        result=result,
        provider=provider,
        model=model,
    )

    await deliver_result(result, delivery)

    return result


# ════════════════════════════════════════════════════════════
# Layer 2: `runEmbeddedPiAgent` – Robustness & Retry
# ════════════════════════════════════════════════════════════

# -- Layer 2 - Data structures

class AttemptError(BaseModel):
    """
    Structured error from run_embedded_attempt.

    Instead of raising raw exceptions, run_embedded_attempt returns
    this object so run_embedded_pi_agent can cleanly check the error
    type without relying on error message strings.

    Corresponds to the combined evaluation of promptError /
    assistantErrorText in OpenClaw (run.ts).
    """
    kind: Literal[
        "context_overflow",        # Context too large for the model
        "thinking_not_supported",  # Model does not support this thinking level
        "aborted",                 # Run was aborted (timeout or signal)
        "other",                   # All other errors
    ]
    message: str
    # Only for thinking_not_supported: the level that was rejected
    rejected_thinking_level: Literal["low", "medium", "high"] | None = None


class AttemptResult(BaseModel):
    """
    The complete result of a single run_embedded_attempt call.

    Cleanly separates success and failure:
    - error is None     → run succeeded, result contains the response
    - error is not None → run reported a known error

    Corresponds to EmbeddedRunAttemptResult in OpenClaw, simplified.
    """
    # Success case
    result: AgentCommandResult | None = None

    # Failure case
    error: AttemptError | None = None

    # How many auto-compactions occurred in this attempt?
    # (run_embedded_attempt counts them internally)
    compaction_count: int = 0

    # Number of messages after the attempt (for diagnostics)
    message_count: int = 0


# -- Layer 2 - Functions -----------------------------------

def pick_fallback_thinking_level(
    current: Literal["low", "medium", "high"],
    attempted: set[Literal["low", "medium", "high"]],
) -> Literal["low", "medium", "high"] | None:
    """
    Selects the next-lower thinking level that has not yet been attempted.

    Logic:
      - Find current in THINKING_LEVEL_ORDER
      - Traverse the list downward (lower levels)
      - Return the first level not in attempted
      - If no level remains: return None

    Examples:
      current="high", attempted={"high"} → "medium"
      current="medium", attempted={"high","medium"} → "low"
      current="low",   attempted={"high","medium","low"} → None

    Corresponds to pickFallbackThinkingLevel() in OpenClaw
    (src/agents/pi-embedded-runner/run.ts).
    """
    try:
        current_index = THINKING_LEVEL_ORDER.index(current)
    except ValueError:
        # Unexpected thinking level, no fallback possible
        return None

    for level in THINKING_LEVEL_ORDER[current_index + 1:]:
        if level not in attempted:
            return level

    return None  # No fallback possible


# -- Layer 2 - run_embedded_pi_agent ----------------------------

# Maximum number of context-overflow compactions per run
MAX_OVERFLOW_COMPACTION_ATTEMPTS = 3


class LoopState(BaseModel):
    compaction_attempts: int = 0
    active_thinking: Literal["low", "medium", "high"] | None = None
    attempted_thinking: set[Literal["low", "medium", "high"]] = Field(default_factory=set)


def handle_aborted(
    opts: "RunEmbeddedPiAgentOptions",
) -> "AgentCommandResult":
    """Returns an error result for an aborted run."""
    trace_and_log(logger, f"Run aborted | run_id={opts.run_id}")
    return _error_result(opts, "Run was aborted.", "aborted", aborted=True)


def repair_thinking_not_supported(
    opts: "RunEmbeddedPiAgentOptions",
    state: LoopState,
) -> tuple[bool, "AgentCommandResult | None"]:
    """
    Handles a thinking-not-supported error: picks a fallback level if available.
    Mutates state.active_thinking on retry. Returns (should_retry, error_result_or_none).
    """
    fallback = pick_fallback_thinking_level(state.active_thinking, state.attempted_thinking)

    if fallback is not None:
        trace_and_log(logger, f"Thinking level '{state.active_thinking}' not supported | run_id={opts.run_id} → falling back to '{fallback}'")
        state.active_thinking = fallback
        return True, None

    trace_and_log(logger, f"No thinking level fallback available | run_id={opts.run_id}")
    return False, _error_result(
        opts,
        "The model does not support any of the requested thinking levels. Please choose a different model.",
        "thinking_not_supported",
    )


async def memory_flush(session_key: str, cwd: str) -> None:
    """Silent agent turn that saves important context to memory/YYYY-MM-DD.md before compaction."""
    today = date.today().isoformat()
    prompt = (
        f"[Memory Flush — silent turn]\n"
        f"Before this session is compacted, save important context to the daily memory file.\n"
        f"Use the `write` tool to save to: memory/{today}.md\n"
        f"If the file already exists, read it first with `memory_get`, then rewrite "
        f"with the existing content plus new entries appended.\n"
        f"Format: concise bullet points. Include key decisions, facts, preferences, "
        f"todos. Skip trivial or ephemeral messages.\n"
        f"If nothing important needs saving, skip the write call entirely.\n"
        f"Do not reply with anything visible to the user."
    )
    try:
        await agent_command(prompt, session_key=session_key, runtime=RuntimeEnv(cwd=cwd))
        logger.info("Memory flush completed | session_key=%s", session_key)
    except Exception as e:
        logger.warning("Memory flush failed | session_key=%s error=%s", session_key, e)


async def repair_context_overflow(
    opts: "RunEmbeddedPiAgentOptions",
    error: AttemptError,
    state: LoopState,
) -> tuple[bool, "AgentCommandResult | None"]:
    """
    Handles a context-overflow error: checks the compaction limit, attempts
    compaction, and signals whether the caller should retry the run.
    Mutates state.compaction_attempts. Returns (should_retry, error_result_or_none).
    """
    trace_and_log(logger, f"Context overflow | run_id={opts.run_id} attempt={state.compaction_attempts + 1}/{MAX_OVERFLOW_COMPACTION_ATTEMPTS} message={error.message[:120]}")

    if state.compaction_attempts >= MAX_OVERFLOW_COMPACTION_ATTEMPTS:
        trace_and_log(logger, f"Context overflow: compaction limit reached | run_id={opts.run_id}")
        return False, _error_result(
            opts,
            "Context overflow: the conversation history is too long. Please start a new session.",
            "context_overflow",
        )

    state.compaction_attempts += 1
    trace_and_log(logger, f"Attempting compaction | run_id={opts.run_id} attempt={state.compaction_attempts}")

    cwd = str(Path(opts.workspace_dir).parent.parent)
    await memory_flush(opts.session_record.session_key, cwd)

    compact_result = await compact_session(
        session_file=opts.session_file,
        config=opts.config,
    )

    if compact_result.compacted:
        trace_and_log(logger, f"Compaction successful | run_id={opts.run_id} tokens_before={compact_result.tokens_before} tokens_after={compact_result.tokens_after} → retrying")
        return True, None

    trace_and_log(logger, f"Compaction produced no reduction | run_id={opts.run_id} reason={compact_result.reason}")
    return False, _error_result(
        opts,
        "Context overflow: compaction not possible. Please start a new session.",
        "context_overflow",
    )


def _error_result(
    opts: "RunEmbeddedPiAgentOptions",
    text: str,
    stop_reason: str,
    aborted: bool = False,
) -> "AgentCommandResult":
    return AgentCommandResult(
        payloads=[RunPayload(text=text, is_error=True)],
        meta=AgentRunMeta(
            session_id=opts.session_record.session_id,
            provider=opts.provider,
            model=opts.model,
            duration_ms=0,
            aborted=aborted,
            stop_reason=stop_reason,
        ),
    )


class RunEmbeddedPiAgentOptions(BaseModel):
    prompt: str
    session_record: SessionRecord
    session_file: str
    workspace_dir: str
    provider: str
    model: str
    thinking_level: Literal["low", "medium", "high"] | None = None
    timeout_ms: int
    run_id: str
    skills_snapshot: SkillsSnapshot | None = None
    config: SelmaConfig
    bootstrap_mode: BootstrapMode = "none"
    abort_signal: asyncio.Event | None = None
    delivery: DeliveryContext = DeliveryContext()
    # None = all tools allowed; list = only the named tools
    tools_allow: list[str] | None = None

    model_config = {"arbitrary_types_allowed": True}


@tracer.chain(name="run_embedded_pi_agent")
async def run_embedded_pi_agent(
    opts: RunEmbeddedPiAgentOptions,
) -> AgentCommandResult:
    """
    Layer 2 – Robustness and retry.

    Answers the question: "How often / under which conditions?"

    Handles (simplified, without auth rotation and overload backoff):
      1. Context overflow  → compact_session() → retry
      2. Thinking level not supported → fallback → retry
      3. No error          → return result

    Corresponds to runEmbeddedPiAgent() in OpenClaw
    (src/agents/pi-embedded-runner/run.ts), heavily simplified.
    """

    state = LoopState(active_thinking=opts.thinking_level)

    while True:
        state.attempted_thinking.add(state.active_thinking)

        attempt = await run_embedded_attempt(
            opts.model_copy(update={"thinking_level": state.active_thinking})
        )

        if attempt.error is None:
            return attempt.result

        error = attempt.error

        match error.kind:
            case "context_overflow":
                should_retry, error_result = await repair_context_overflow(opts, error, state)
            case "thinking_not_supported":
                should_retry, error_result = repair_thinking_not_supported(opts, state)
            case "aborted":
                should_retry, error_result = False, handle_aborted(opts)
            case _:
                trace_and_log(logger, f"Unknown error in attempt | run_id={opts.run_id} message={error.message}")
                raise RuntimeError(f"run_embedded_attempt failed: {error.message}")

        if should_retry:
            continue
        return error_result


# ════════════════════════════════════════════════════════════
# Layer 3: Execution - `runEmbeddedAttempt`
# ════════════════════════════════════════════════════════════
#
# New data structures and run_embedded_attempt().
# Belongs in the existing runtime.py, inserted directly before
# agent_command().
#
# Simplifications compared to OpenClaw:
#   - no sandbox
#   - no ACP
#   - Ollama only (no StreamFn wrapping)
# ============================================================

# -- Layer 3 - Data structures

# RuntimeInfo and EmbeddedContextFile come from system_prompt.py (imported above).
# BuildAgentSystemPromptParams as well.


class CollectedOutput(BaseModel):
    """
    Everything collected from agent events during a run.

    run_embedded_attempt subscribes to agent events and fills
    this object — analogous to subscribeEmbeddedPiSession()
    in OpenClaw (src/agents/pi-embedded-runner/run/attempt.ts).
    """
    # All text fragments from message_update events (streaming)
    text_parts: list[str] = Field(default_factory=list)

    # Names of all tools called during the run
    tool_names_used: list[str] = Field(default_factory=list)

    @property
    def final_text(self) -> str:
        """Fully assembled response text."""
        return "".join(self.text_parts)


class PromptResult(BaseModel):
    aborted: bool = False
    timed_out: bool = False
    run_exception: Exception | None = None

    model_config = {"arbitrary_types_allowed": True}


# -- Layer 3 - Functions


def _load_context_files(workspace_dir: str) -> list[EmbeddedContextFile]:
    """
    Loads all workspace context files (AGENTS.md, SOUL.md, etc.)
    via ResourceLoader and converts them to EmbeddedContextFile objects.

    EmbeddedContextFile comes from system_prompt.py.
    ResourceLoader comes from my_mono/resource_loader.py.
    """
    # workspace_dir is <root>/.selma/workspace; ResourceLoader expects the project root.
    cwd = str(Path(workspace_dir).parent.parent)
    loader = ResourceLoader(cwd=cwd)
    context_files_raw = loader.load_context_files()
    return [
        EmbeddedContextFile(path=cf.path, content=cf.content)
        for cf in context_files_raw
    ]


def detect_attempt_error(
    final_text: str | None,
    exception: Exception | None,
) -> AttemptError | None:
    """
    Analyses the output text and an optional exception and
    classifies known error types.

    Recognised patterns:
      context_overflow:
        - Exception message contains: "context length", "context window",
          "maximum context", "token limit", "prompt is too long"
        - Response text contains the same patterns (Ollama sometimes
          returns these as regular text)

      thinking_not_supported:
        - Exception message contains: "reasoning_effort",
          "thinking is not supported", "does not support thinking"

    Returns None if no known error was detected.

    Corresponds to the error classification in run.ts (isLikelyContextOverflowError,
    classifyFailoverReason) in OpenClaw.
    """
    if exception is not None:
        message = str(exception).lower()
        if any(kw in message for kw in [
            "context length", "context window", "maximum context",
            "token limit", "prompt is too long"
        ]):
            return AttemptError(
                kind="context_overflow",
                message=str(exception),
            )
        if any(kw in message for kw in [
            "reasoning_effort", "thinking is not supported",
            "does not support thinking"
        ]):
            # Optionally extract the rejected thinking level
            rejected_level = None
            for level in ["low", "medium", "high"]:
                if level in message:
                    rejected_level = level
                    break

            return AttemptError(
                kind="thinking_not_supported",
                message=str(exception),
                rejected_thinking_level=rejected_level,
            )

    if final_text is not None:
        text = final_text.lower()
        if any(kw in text for kw in [
            "context length", "context window", "maximum context",
            "token limit", "prompt is too long"
        ]):
            return AttemptError(
                kind="context_overflow",
                message=final_text,
            )
        if any(kw in text for kw in [
            "reasoning_effort", "thinking is not supported",
            "does not support thinking"
        ]):
            rejected_level = None
            for level in ["low", "medium", "high"]:
                if level in text:
                    rejected_level = level
                    break

            return AttemptError(
                kind="thinking_not_supported",
                message=final_text,
                rejected_thinking_level=rejected_level,
            )

    return None  # No known error detected


def subscribe_output_collector(
    delivery: DeliveryContext,
    session: AgentSession,
) -> tuple[CollectedOutput, Callable[[], None]]:
    """
    Wires up the event handler, subscribes it to the session, and returns
    (output, unsubscribe). Handles message streaming, block chunking, and
    tool-name collection.
    """
    output = CollectedOutput()
    chunking_cfg = delivery.block_reply_chunking or BlockReplyChunkingConfig()
    chunker = _BlockChunker(chunking_cfg, delivery.on_block_reply) if delivery.on_block_reply else None

    def on_event(event: AgentEvent) -> None:
        if event.type == "message_update" and isinstance(event.payload, str):
            output.text_parts.append(event.payload)
            if delivery.on_partial_reply:
                delivery.on_partial_reply(event.payload)
            if chunker:
                chunker.feed(event.payload)
        elif event.type == "tool_start" and event.payload is not None:
            tool_name = getattr(event.payload, "name", str(event.payload))
            tool_args = getattr(event.payload, "arguments", {})
            output.tool_names_used.append(tool_name)
            if delivery.on_tool_call:
                delivery.on_tool_call(tool_name, tool_args)
        elif event.type == "agent_end":
            if chunker:
                chunker.flush()
            if delivery.on_block_reply_flush:
                delivery.on_block_reply_flush()

    unsubscribe = session.subscribe(on_event)
    return output, unsubscribe


async def execute_prompt(
    session: AgentSession,
    effective_prompt: str,
    abort_signal: asyncio.Event | None,
    timeout_ms: int,
    run_id: str,
) -> PromptResult:
    """
    Runs session.prompt() with timeout and abort-signal handling.
    Returns a PromptResult; never raises — exceptions are captured in run_exception.
    """
    result = PromptResult()
    try:
        prompt_coro = session.prompt(effective_prompt)

        if abort_signal is not None and abort_signal.is_set():
            result.aborted = True
        elif timeout_ms > 0:
            timeout_sec = timeout_ms / 1000.0
            try:
                await asyncio.wait_for(prompt_coro, timeout=timeout_sec)
            except asyncio.TimeoutError:
                result.timed_out = True
                result.aborted = True
                trace_and_log(logger, f"Timeout after {timeout_sec:.1f}s | run_id={run_id}")
        else:
            await prompt_coro

    except asyncio.CancelledError:
        result.aborted = True
        trace_and_log(logger, f"CancelledError | run_id={run_id}")

    except Exception as exc:
        result.run_exception = exc
        trace_and_log(logger, f"Error in session.prompt | run_id={run_id} error={exc}")

    return result


def build_attempt_result(
    prompt_result: PromptResult,
    output: CollectedOutput,
    session: AgentSession,
    opts: RunEmbeddedPiAgentOptions,
    duration_ms: int,
) -> AttemptResult:
    message_count = len(session.state.messages)

    if prompt_result.aborted:
        reason = "Timeout" if prompt_result.timed_out else "Aborted"
        return AttemptResult(
            error=AttemptError(kind="aborted", message=reason),
            message_count=message_count,
        )

    attempt_error = detect_attempt_error(
        final_text=output.final_text,
        exception=prompt_result.run_exception,
    )
    if attempt_error is not None:
        trace_and_log(logger, f"Known error detected | run_id={opts.run_id} kind={attempt_error.kind}")
        return AttemptResult(
            error=attempt_error,
            message_count=message_count,
        )

    if prompt_result.run_exception is not None:
        raise prompt_result.run_exception

    return AttemptResult(
        result=AgentCommandResult(
            payloads=[RunPayload(text=output.final_text)],
            meta=AgentRunMeta(
                session_id=session.session_id or opts.session_record.session_id,
                provider=opts.provider,
                model=opts.model,
                duration_ms=duration_ms,
                aborted=False,
                stop_reason="end_turn",
            ),
        ),
        message_count=message_count,
    )


# Layer 3 - run_embedded_attempt

@tracer.chain(name="run_embedded_attempt")
async def run_embedded_attempt(
    opts: RunEmbeddedPiAgentOptions,
) -> AttemptResult:
    """
    Layer 3 – Execution of a single agent turn.

    Answers the question: "What exactly happens in one run?"

    Corresponds to runEmbeddedAttempt() in OpenClaw
    (src/agents/pi-embedded-runner/run/attempt.ts), heavily simplified.
    Simplifications: no sandbox, no ACP, Ollama only.
    """
    started_at = now_ms()

    add_span_infos(run_id=opts.run_id, model=opts.model, thinking_level=opts.thinking_level, bootstrap_mode=opts.bootstrap_mode)

    session_key_parts = opts.session_record.session_key.split(":")
    channel = session_key_parts[-1] if len(session_key_parts) >= 3 else None

    runtime_info = RuntimeInfo(
        agent_id=opts.config.agent.id,
        host=platform.node(),
        model=f"{opts.provider}/{opts.model}",
        default_model=f"{opts.provider}/{opts.model}",
        os=f"{platform.system()} {platform.release()}",
        arch=platform.machine(),
        shell=os.environ.get("SHELL", ""),
        channel=channel,
    )

    context_files = _load_context_files(opts.workspace_dir)
    
    system_prompt = build_agent_system_prompt(
        BuildAgentSystemPromptParams(
            workspace_dir=os.path.abspath(opts.workspace_dir),
            tool_names=opts.tools_allow if opts.tools_allow is not None else ALL_TOOL_NAMES,
            context_files=context_files,
            skills_prompt=opts.skills_snapshot.snapshot_text if opts.skills_snapshot else None,
            runtime_info=runtime_info,
            default_think_level=opts.thinking_level,
            bootstrap_mode=opts.bootstrap_mode,
        )
    )

    # -- Bootstrap prefix for first user turn
    bootstrap_prefix = build_agent_user_prompt_prefix(opts.bootstrap_mode)
    effective_prompt = (
        f"{bootstrap_prefix}\n\n{opts.prompt}" if bootstrap_prefix else opts.prompt
    )

    # --Create AgentSession
    all_tools = create_selma_tools(opts.workspace_dir, config=opts.config)
    if opts.tools_allow is not None:
        allowed = set(opts.tools_allow)
        active_tools = [t for t in all_tools if t.name in allowed]
    else:
        active_tools = all_tools

    session_path = Path(opts.session_file)
    session_manager = AgentSessionManager(session_file=session_path)
    session = await create_agent_session(
        CreateSessionOptions(
            model=opts.model,
            system_prompt=system_prompt,
            thinking_level=opts.thinking_level,
            ollama_base_url=opts.config.model.ollama_base_url,
            cwd=opts.workspace_dir,
            session_manager=session_manager,
            tools=active_tools,
        )
    )

    output, unsubscribe = subscribe_output_collector(opts.delivery, session)

    # -- Execute prompt (with timeout and abort)
    try:
        prompt_result = await execute_prompt(
            session, effective_prompt,
            abort_signal=opts.abort_signal,
            timeout_ms=opts.timeout_ms,
            run_id=opts.run_id,
        )
    finally:
        unsubscribe()

    duration_ms = now_ms() - started_at

    return build_attempt_result(prompt_result, output, session, opts, duration_ms)


