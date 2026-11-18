from __future__ import annotations

import asyncio
import logging
import os
import platform
from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field

from selma.agent import AgentEvent, AgentTool
from selma.agent_session import (
    AgentSession,
    CreateSessionOptions,
    create_agent_session,
)
from selma.agent_session import (
    SessionManager as AgentSessionManager,
)
from selma.compaction import compact_session
from selma.config import (
    THINKING_LEVEL_ORDER,
    SelmaConfig,
)
from selma.helper import now_ms
from selma.resource_loader import ResourceLoader
from selma.session_store import (
    SessionRecord,
    SkillsSnapshot,
)
from selma.system_prompt import (
    BootstrapMode,
    BuildAgentSystemPromptParams,
    EmbeddedContextFile,
    RuntimeInfo,
    build_agent_system_prompt,
    build_agent_user_prompt_prefix,
)
from selma.tools import ALL_TOOL_NAMES, create_selma_tools
from selma.tracing import add_span_infos, trace_and_log, tracer

from .common import (
    AgentCommandResult,
    AgentRunMeta,
    BlockReplyChunkingConfig,
    DeliveryContext,
    RunPayload,
    _BlockChunker,  # pyright: ignore[reportPrivateUsage]
)

logger = logging.getLogger("selma.runtime")


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
        "context_overflow",  # Context too large for the model
        "thinking_not_supported",  # Model does not support this thinking level
        "aborted",  # Run was aborted (timeout or signal)
        "other",  # All other errors
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


def pick_fallback_thinking_level(
    current: Literal["low", "medium", "high"] | None,
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
    if current is None:
        return None

    try:
        current_index = THINKING_LEVEL_ORDER.index(current)
    except ValueError:
        # Unexpected thinking level, no fallback possible
        return None

    for level in THINKING_LEVEL_ORDER[current_index + 1 :]:
        if level not in attempted:
            return cast(Literal["low", "medium", "high"], level)

    return None  # No fallback possible


MAX_OVERFLOW_COMPACTION_ATTEMPTS = 3


class LoopState(BaseModel):
    compaction_attempts: int = 0
    active_thinking: Literal["low", "medium", "high"] | None = None
    attempted_thinking: set[Literal["low", "medium", "high"]] = cast(
        set[Literal["low", "medium", "high"]], Field(default_factory=set)
    )


def handle_aborted(
    opts: RunEmbeddedPiAgentOptions,
) -> AgentCommandResult:
    """Returns an error result for an aborted run."""
    trace_and_log(logger, f"Run aborted | run_id={opts.run_id}")
    return _error_result(opts, "Run was aborted.", "aborted", aborted=True)


def repair_thinking_not_supported(
    opts: RunEmbeddedPiAgentOptions,
    state: LoopState,
) -> tuple[bool, AgentCommandResult | None]:
    """
    Handles a thinking-not-supported error: picks a fallback level if available.
    Mutates state.active_thinking on retry. Returns (should_retry, error_result_or_none).
    """
    fallback = pick_fallback_thinking_level(state.active_thinking, state.attempted_thinking)

    if fallback is not None:
        trace_and_log(
            logger,
            f"Thinking level '{state.active_thinking}' not supported | run_id={opts.run_id} → falling back to '{fallback}'",
        )
        state.active_thinking = fallback
        return True, None

    trace_and_log(logger, f"No thinking level fallback available | run_id={opts.run_id}")
    return False, _error_result(
        opts,
        "The model does not support any of the requested thinking levels. Please choose a different model.",
        "thinking_not_supported",
    )


async def repair_context_overflow(
    opts: RunEmbeddedPiAgentOptions,
    error: AttemptError,
    state: LoopState,
) -> tuple[bool, AgentCommandResult | None]:
    """
    Handles a context-overflow error: checks the compaction limit, attempts
    compaction, and signals whether the caller should retry the run.
    Mutates state.compaction_attempts. Returns (should_retry, error_result_or_none).
    """
    trace_and_log(
        logger,
        f"Context overflow | run_id={opts.run_id} attempt={state.compaction_attempts + 1}/{MAX_OVERFLOW_COMPACTION_ATTEMPTS} message={error.message[:120]}",
    )

    if state.compaction_attempts >= MAX_OVERFLOW_COMPACTION_ATTEMPTS:
        trace_and_log(logger, f"Context overflow: compaction limit reached | run_id={opts.run_id}")
        return False, _error_result(
            opts,
            "Context overflow: the conversation history is too long. Please start a new session.",
            "context_overflow",
        )

    state.compaction_attempts += 1
    trace_and_log(logger, f"Attempting compaction | run_id={opts.run_id} attempt={state.compaction_attempts}")

    cwd = opts.workspace_dir  # workspace_dir ist der Projekt-Root (Agent-CWD)
    from selma.runtime.command import memory_flush

    await memory_flush(opts.session_record.session_key, cwd)

    compact_result = await compact_session(
        session_file=opts.session_file,
        config=opts.config,
    )

    if compact_result.compacted:
        trace_and_log(
            logger,
            f"Compaction successful | run_id={opts.run_id} tokens_before={compact_result.tokens_before} tokens_after={compact_result.tokens_after} → retrying",
        )
        return True, None

    trace_and_log(logger, f"Compaction produced no reduction | run_id={opts.run_id} reason={compact_result.reason}")
    return False, _error_result(
        opts,
        "Context overflow: compaction not possible. Please start a new session.",
        "context_overflow",
    )


def _error_result(
    opts: RunEmbeddedPiAgentOptions,
    text: str,
    stop_reason: str,
    aborted: bool = False,
) -> AgentCommandResult:
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
    # True only for the very first turn of a (new/reset) session — controls
    # whether the bootstrap user-prompt prefix is injected (see run_embedded_attempt).
    is_new_session: bool = False
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
        if state.active_thinking is not None:
            state.attempted_thinking.add(state.active_thinking)

        attempt = await run_embedded_attempt(opts.model_copy(update={"thinking_level": state.active_thinking}))

        if attempt.error is None:
            # Invariant: a successful attempt always carries a result.
            assert attempt.result is not None
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
        # Invariant: a non-retry path always produces an error result
        # (repair_* return (False, result) and never (False, None)).
        assert error_result is not None
        return error_result


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


def _load_context_files(workspace_dir: str) -> list[EmbeddedContextFile]:
    """
    Loads all workspace context files (AGENTS.md, SOUL.md, etc.)
    via ResourceLoader and converts them to EmbeddedContextFile objects.

    EmbeddedContextFile comes from system_prompt.py.
    ResourceLoader comes from my_resource_loader.py.
    """
    # workspace_dir ist der Projekt-Root; ResourceLoader arbeitet direkt darauf.
    loader = ResourceLoader(cwd=workspace_dir)
    context_files_raw = loader.load_context_files()
    return [EmbeddedContextFile(path=cf.path, content=cf.content) for cf in context_files_raw]


_THINKING_LEVELS: list[Literal["low", "medium", "high"]] = ["low", "medium", "high"]


def _extract_rejected_level(text: str) -> Literal["low", "medium", "high"] | None:
    """Returns the first thinking level ('low'/'medium'/'high') mentioned in text, else None."""
    for level in _THINKING_LEVELS:
        if level in text:
            return level
    return None


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
        if any(
            kw in message
            for kw in ["context length", "context window", "maximum context", "token limit", "prompt is too long"]
        ):
            return AttemptError(
                kind="context_overflow",
                message=str(exception),
            )
        if any(kw in message for kw in ["reasoning_effort", "thinking is not supported", "does not support thinking"]):
            # Optionally extract the rejected thinking level
            return AttemptError(
                kind="thinking_not_supported",
                message=str(exception),
                rejected_thinking_level=_extract_rejected_level(message),
            )

    if final_text is not None:
        text = final_text.lower()
        if any(
            kw in text
            for kw in ["context length", "context window", "maximum context", "token limit", "prompt is too long"]
        ):
            return AttemptError(
                kind="context_overflow",
                message=final_text,
            )
        if any(kw in text for kw in ["reasoning_effort", "thinking is not supported", "does not support thinking"]):
            return AttemptError(
                kind="thinking_not_supported",
                message=final_text,
                rejected_thinking_level=_extract_rejected_level(text),
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
            except TimeoutError:
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


def _build_runtime_info(opts: RunEmbeddedPiAgentOptions) -> RuntimeInfo:
    """RuntimeInfo for the system prompt (host/model/shell metadata)."""
    session_key_parts = opts.session_record.session_key.split(":")
    channel = session_key_parts[-1] if len(session_key_parts) >= 3 else None

    return RuntimeInfo(
        agent_id=opts.config.agent.id,
        host=platform.node(),
        model=f"{opts.provider}/{opts.model}",
        default_model=f"{opts.provider}/{opts.model}",
        os=f"{platform.system()} {platform.release()}",
        arch=platform.machine(),
        shell=os.environ.get("SHELL", ""),
        channel=channel,
    )


def _build_attempt_system_prompt(
    opts: RunEmbeddedPiAgentOptions,
    runtime_info: RuntimeInfo,
    context_files: list[EmbeddedContextFile],
) -> str:
    """Layer-3 system prompt (built per attempt; context files reloaded each turn)."""
    return build_agent_system_prompt(
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


def _resolve_effective_prompt(opts: RunEmbeddedPiAgentOptions) -> str:
    """Effective user prompt incl. optional bootstrap prefix (first turn only)."""
    # -- Bootstrap prefix: only for the first turn of a session, otherwise the
    # model gets nudged into repeating its opening greeting on every turn
    # instead of continuing the conversation (BOOTSTRAP.md itself is still
    # available via context_files for the rest of the bootstrap conversation).
    bootstrap_prefix = build_agent_user_prompt_prefix(opts.bootstrap_mode) if opts.is_new_session else None
    return f"{bootstrap_prefix}\n\n{opts.prompt}" if bootstrap_prefix else opts.prompt


def _resolve_active_tools(workspace_dir: str, opts: RunEmbeddedPiAgentOptions) -> list[AgentTool]:
    """All configured tools, filtered by tools_allow when set."""
    all_tools = create_selma_tools(workspace_dir, config=opts.config)
    if opts.tools_allow is not None:
        allowed = set(opts.tools_allow)
        active_tools = [t for t in all_tools if t.name in allowed]
    else:
        active_tools = all_tools
    return active_tools


async def _create_attempt_session(
    system_prompt: str,
    tools: list[AgentTool],
    opts: RunEmbeddedPiAgentOptions,
) -> AgentSession:
    """Fresh AgentSession on opts.session_file (manager per attempt = reset-able)."""
    session_manager = AgentSessionManager(session_file=Path(opts.session_file))
    return await create_agent_session(
        CreateSessionOptions(
            model=opts.model,
            system_prompt=system_prompt,
            thinking_level=opts.thinking_level,
            ollama_base_url=opts.config.model.ollama_base_url,
            client_timeout_seconds=opts.config.model.timeout_seconds,
            client_max_retries=opts.config.model.client_max_retries,
            cwd=opts.workspace_dir,
            session_manager=session_manager,
            tools=tools,
        )
    )


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

    add_span_infos(
        run_id=opts.run_id, model=opts.model, thinking_level=opts.thinking_level, bootstrap_mode=opts.bootstrap_mode
    )

    runtime_info = _build_runtime_info(opts)
    context_files = _load_context_files(opts.workspace_dir)
    system_prompt = _build_attempt_system_prompt(opts, runtime_info, context_files)
    effective_prompt = _resolve_effective_prompt(opts)
    active_tools = _resolve_active_tools(opts.workspace_dir, opts)

    # --Create AgentSession
    session = await _create_attempt_session(system_prompt, active_tools, opts)

    output, unsubscribe = subscribe_output_collector(opts.delivery, session)

    # -- Execute prompt (with timeout and abort)
    try:
        prompt_result = await execute_prompt(
            session,
            effective_prompt,
            abort_signal=opts.abort_signal,
            timeout_ms=opts.timeout_ms,
            run_id=opts.run_id,
        )
    finally:
        unsubscribe()

    duration_ms = now_ms() - started_at

    return build_attempt_result(prompt_result, output, session, opts, duration_ms)
