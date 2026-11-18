from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import date
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel

from selma.config import (
    SelmaConfig,
    get_default_model,
    load_config,
    resolve_thinking_default,
    resolve_timeout,
    resolve_tools_allow,
)
from selma.delivery import deliver_result
from selma.helper import get_workspace, now_iso, now_ms
from selma.session_store import (
    SessionRecord,
    SessionRef,
    SessionStore,
    SkillsSnapshot,
    is_session_fresh,
    load_session_store,
    reset_session,
    resolve_session,
    resolve_session_file,
    save_session_store,
    update_session_store_after_run,
)
from selma.skills import build_skill_snapshot, get_skills_snapshot_version
from selma.system_prompt import (
    BootstrapMode,
)
from selma.tracing import add_span_infos, trace_and_log, tracer

from .attempt import RunEmbeddedPiAgentOptions, run_embedded_pi_agent
from .common import (
    AgentCommandResult,
    DeliveryContext,
    LifecyclePhase,
    RuntimeEnv,
    emit_lifecycle_event,
)

logger = logging.getLogger("selma.runtime")


def _resolve_skills_snapshot(
    session_record: SessionRecord,
    workspace_dir: str,
    is_new_session: bool,
) -> SkillsSnapshot | None:
    current_version = get_skills_snapshot_version(workspace_dir)
    needs_refresh = (
        is_new_session
        or session_record.skills_snapshot is None
        or session_record.skills_snapshot.version != current_version
    )
    if not needs_refresh:
        # mypy cannot narrow through the derived `needs_refresh` bool
        # (needs_refresh False ⇒ snapshot is not None), so branch on it directly.
        return session_record.skills_snapshot
    snapshot = build_skill_snapshot(workspace_dir, current_version)
    session_record.skills_snapshot = snapshot
    trace_and_log(
        logger,
        f"Skills snapshot rebuilt | version={current_version} skills={snapshot.skill_names}",
    )
    return snapshot


def get_session(
    ref: SessionRef,
    config: SelmaConfig,
    cwd: str,
) -> tuple[SessionStore, SessionRecord, bool, str]:
    """
    Loads the session store, resolves the session, resets it if stale,
    and determines the session file path.
    Returns (store, session_record, is_new_session, session_file).
    """
    store = load_session_store(cwd=cwd)
    session_record, is_new_session = resolve_session(store, ref, config)

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


class CommandContext(BaseModel):
    """
    Bundles everything agent_command derives during its preparation phase.

    The 6 public parameters (most optional) were the smell; this data-carrier
    holds the validation results plus per-run state (config, session,
    model choice, timeouts) as one object.

    Non-optional fields are guaranteed by _prepare_command; the optional
    ones mirror the corresponding RunEmbeddedPiAgentOptions fields.

    model_config allows arbitrary types (asyncio.Event, Callables inside
    DeliveryContext).
    """

    run_id: str
    started_at: int
    # workspace_dir ist ab sofort immer das aktuelle Arbeitsverzeichnis, nicht das .selma-Verzeichnis
    workspace_dir: str
    delivery: DeliveryContext
    config: SelmaConfig
    store: SessionStore
    session_record: SessionRecord
    is_new_session: bool
    session_file: str
    provider: str
    model: str
    timeout_ms: int
    bootstrap_mode: BootstrapMode
    # session_key + session_id als ein Referenz-Paar (P3#14), gesetzt in _prepare_command
    session_ref: SessionRef
    thinking_level: str | None = None
    skills_snapshot: SkillsSnapshot | None = None
    abort_signal: asyncio.Event | None = None
    tools_allow: list[str] | None = None

    model_config = {"arbitrary_types_allowed": True}


def _prepare_command(
    message: str,
    *,
    session_key: str | None,
    session_id: str | None,
    workspace_dir: str,
    delivery: DeliveryContext,
    abort_signal: asyncio.Event | None,
) -> CommandContext:
    """
    Phase 1 – Validation und Kontext-Auflösung (vor dem "start"-Event,
    damit ValueError-Abbrüche die Listener nicht berühren).

    Mutiert die Session (updated_at, skills snapshot) exakt wie die
    frühere monolithische agent_command — die Nebenwirkungen Teil des
    Vertrags.
    """
    if not message.strip():
        raise ValueError("message must not be empty")

    if not session_key and not session_id:
        raise ValueError("At least session_key or session_id must be provided")

    run_id = str(uuid.uuid4())[:8]
    started_at = now_ms()

    session_ref = SessionRef(session_key=session_key, session_id=session_id)
    add_span_infos(run_id=run_id, session_key=session_ref.session_key)

    config = load_config(workspace_dir)

    store, session_record, is_new_session, session_file = get_session(
        session_ref,
        config,
        workspace_dir,
    )

    bootstrap_mode = detect_bootstrap_mode(
        get_workspace(workspace_dir)
    )  # BOOTSTRAP.md exists → "full" access, missing → "none"

    # skills.py auflöst intern selbst <root>/.selma/workspace/skills (get_workspace).
    skills_snapshot = _resolve_skills_snapshot(session_record, workspace_dir, is_new_session)

    session_record.updated_at = now_iso()
    save_session_store(store)

    default_provider, default_model = get_default_model(config)
    provider = session_record.provider_override or default_provider
    model = session_record.model_override or default_model

    thinking_level = session_record.thinking_level or resolve_thinking_default(config, provider, model)

    trace_and_log(
        logger,
        f"Model | provider={provider} model={model} thinking={thinking_level}",
    )

    timeout_seconds = resolve_timeout(config)
    timeout_ms = timeout_seconds * 1000 if timeout_seconds > 0 else 0

    tools_allow = resolve_tools_allow(config)

    return CommandContext(
        run_id=run_id,
        started_at=started_at,
        workspace_dir=workspace_dir,
        delivery=delivery,
        config=config,
        store=store,
        session_record=session_record,
        is_new_session=is_new_session,
        session_file=session_file,
        provider=provider,
        model=model,
        bootstrap_mode=bootstrap_mode,
        thinking_level=thinking_level,
        skills_snapshot=skills_snapshot,
        session_ref=session_ref,
        abort_signal=abort_signal,
        timeout_ms=timeout_ms,
        tools_allow=tools_allow,
    )


def _build_run_options(message: str, ctx: CommandContext) -> RunEmbeddedPiAgentOptions:
    """
    Phase 2 – baut die Layer-2-Options aus dem vorbereiteten Kontext.
    """
    return RunEmbeddedPiAgentOptions(
        prompt=message,
        session_record=ctx.session_record,
        session_file=ctx.session_file,
        workspace_dir=ctx.workspace_dir,
        provider=ctx.provider,
        model=ctx.model,
        skills_snapshot=ctx.skills_snapshot,
        config=ctx.config,
        bootstrap_mode=ctx.bootstrap_mode,
        is_new_session=ctx.is_new_session,
        abort_signal=ctx.abort_signal,
        thinking_level=cast(Literal["low", "medium", "high"] | None, ctx.thinking_level),
        timeout_ms=ctx.timeout_ms,
        run_id=ctx.run_id,
        delivery=ctx.delivery,
        tools_allow=ctx.tools_allow,
    )


async def _execute_command(opts: RunEmbeddedPiAgentOptions, *, started_at: int) -> AgentCommandResult:
    """
    Phase 2.5 – Layer-2-Call inkl. Lifecycle-Events.

    Erfolg: "end"-Event. Fehler: "error"-Event (nur wenn noch kein
    "end" emittiert wurde) + Log + Re-Raise — exakt die Semantik der
    alten try/except-Behandlung von agent_command.
    """
    lifecycle_ended = False
    result: AgentCommandResult
    try:
        # Layer 2: run_embedded_pi_agent handles retry/fallback
        result = await run_embedded_pi_agent(opts)

        stop_reason = result.meta.stop_reason
        emit_lifecycle_event(
            LifecyclePhase(
                run_id=opts.run_id,
                phase="end",
                started_at=started_at,
                ended_at=now_ms(),
                aborted=result.meta.aborted,
                stop_reason=stop_reason,
            )
        )
        lifecycle_ended = True
    except Exception as err:
        if not lifecycle_ended:
            emit_lifecycle_event(
                LifecyclePhase(
                    run_id=opts.run_id,
                    phase="error",
                    started_at=started_at,
                    ended_at=now_ms(),
                    error=str(err),
                )
            )
        logger.exception("agentCommand failed | run_id=%s", opts.run_id)
        raise
    return result


async def _finalize_command(result: AgentCommandResult, ctx: CommandContext) -> None:
    """
    Phase 3 – After-Run: Store-Aktualisierung + Lieferung der Antwort.
    """
    update_session_store_after_run(
        store=ctx.store,
        session_record=ctx.session_record,
        provider=ctx.provider,
        model=ctx.model,
    )
    await deliver_result(result, ctx.delivery)


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

    Ablauf (P2#9): _prepare_command (Validation + Kontext) → "start"-Event
    → _build_run_options + _execute_command (Layer 2) →
    _finalize_command (Store + Delivery).
    """
    runtime = runtime or RuntimeEnv()
    delivery = delivery or DeliveryContext()
    # workspace_dir ist ab sofort immer das aktuelle Arbeitsverzeichnis, nicht das .selma-Verzeichnis
    workspace_dir = runtime.cwd

    ctx = _prepare_command(
        message,
        session_key=session_key,
        session_id=session_id,
        workspace_dir=workspace_dir,
        delivery=delivery,
        abort_signal=abort_signal,
    )

    emit_lifecycle_event(
        LifecyclePhase(
            run_id=ctx.run_id,
            phase="start",
            started_at=ctx.started_at,
        )
    )

    opts = _build_run_options(message, ctx)
    result = await _execute_command(opts, started_at=ctx.started_at)

    await _finalize_command(result, ctx)

    return result


async def memory_flush(session_key: str, cwd: str) -> None:
    """Silent agent turn that saves important context to .selma/workspace/memory/YYYY-MM-DD.md before compaction.

    Note: the write tool resolves paths against <root> (the repo/workspace cwd),
    so the prompt points at the full workspace-relative location
    ".selma/workspace/memory/YYYY-MM-DD.md" — deliberately NOT "memory/YYYY-MM-DD.md"
    (which would create a stray <root>/memory/ directory next to the repo).
    """
    today = date.today().isoformat()
    prompt = (
        f"[Memory Flush — silent turn]\n"
        f"Before this session is compacted, save important context to the daily memory file.\n"
        f"Use the `write` tool to save to: .selma/workspace/memory/{today}.md\n"
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
