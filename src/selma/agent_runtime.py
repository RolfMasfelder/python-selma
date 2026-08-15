# ============================================================
# agent_runtime.py
# ============================================================

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from selma.data import NormalizedTurnInput
from selma.my_mono.agent import AgentEvent, AgentTool
from selma.my_mono.agent_session import AgentSession, CreateSessionOptions, create_agent_session
from selma.my_mono.system_prompt import BuildSystemPromptOptions, ContextFile, build_system_prompt
from selma.resource_loader import ResourceLoader

logger = logging.getLogger(__name__)


# ── data structures


class RunResult(BaseModel):
    """Result of a completed agent run."""

    run_id: str
    session_key: str
    status: Literal["ok", "error", "timeout"]
    reply: str | None
    started_at: datetime
    ended_at: datetime
    error: str | None = None


class RunParams(BaseModel):
    """Input parameters for a run."""

    ctx: NormalizedTurnInput
    workspace_dir: str
    tools: list[AgentTool] = Field(default_factory=list)
    model: str | None = None
    timeout_ms: int = 120_000
    light_context: bool = False  # True → only HEARTBEAT.md in system prompt
    on_block_reply: Callable[[str], Awaitable[None]] | None = Field(default=None, exclude=True)
    # Called for every text chunk (message_update). Used by the SSE stream endpoint.
    on_chunk: Callable[[str], Awaitable[None]] | None = Field(default=None, exclude=True)

    model_config = {"arbitrary_types_allowed": True}


# ── RunLaneManager


class RunLaneManager:
    """
    Serializes runs per session key.
    Prevents race conditions when two messages arrive
    simultaneously for the same session.
    """

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._active: dict[str, str] = {}  # session_key → run_id

    def get_lock(self, session_key: str) -> asyncio.Lock:
        if session_key not in self._locks:
            self._locks[session_key] = asyncio.Lock()
        return self._locks[session_key]

    def mark_active(self, session_key: str, run_id: str) -> None:
        self._active[session_key] = run_id
        logger.debug("Lane active | session=%s run_id=%s", session_key, run_id)

    def mark_done(self, session_key: str) -> None:
        self._active.pop(session_key, None)
        logger.debug("Lane done | session=%s", session_key)

    def get_active_run_id(self, session_key: str) -> str | None:
        return self._active.get(session_key)


# ── SessionFactory


class SessionFactory:
    """
    Cached wrapper around create_agent_session() from my-mono.
    my-mono remains completely unchanged.
    """

    def __init__(self):
        self._cache: dict[str, AgentSession] = {}

    async def get_or_create(
        self,
        session_key: str,
        workspace_dir: str,
        system_prompt: str,
        tools: list[AgentTool],
        model: str | None,
    ) -> AgentSession:
        """Returns cached session or creates a new one."""

        if session_key in self._cache:
            session = self._cache[session_key]
            # System prompt may change per turn → update it
            session.agent._options.system_prompt = system_prompt
            logger.debug("Session cache hit | session=%s", session_key)
            return session

        # Store session file in the workspace
        session_file = Path(workspace_dir) / ".my_mono" / "sessions" / f"{session_key}.jsonl"
        session_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Session create | session=%s file=%s", session_key, session_file)

        session = await create_agent_session(
            CreateSessionOptions(
                model=model,
                system_prompt=system_prompt,
                tools=tools,
                cwd=workspace_dir,
                continue_session=session_file if session_file.exists() else None,
            )
        )

        self._cache[session_key] = session
        return session

    def invalidate(self, session_key: str) -> None:
        """Remove session from cache — e.g. after /reset."""
        if session_key in self._cache:
            del self._cache[session_key]
            logger.info("Session invalidated | session=%s", session_key)


# ── SystemPromptBuilder


class SystemPromptBuilder:
    """
    Builds the OpenClaw system prompt from workspace files.
    Calls build_system_prompt() from my-mono internally —
    extended with SOUL.md, TOOLS.md, Safety, and Runtime.
    my-mono/system_prompt.py remains unchanged.
    """

    MAX_CHARS_PER_FILE = 20_000

    def build(
        self,
        workspace_dir: str,
        ctx: NormalizedTurnInput,
        tools: list[AgentTool],
        light_context: bool = False,
    ) -> str:
        # 1. Base via my-mono (tool list + guidelines only)
        base = build_system_prompt(
            BuildSystemPromptOptions(
                cwd=workspace_dir,
                selected_tools=[t.name for t in tools],
                context_files=[],  # loaded separately via ResourceLoader
            )
        )

        # 2. Safety
        safety = (
            "\n\n## Safety\n"
            "Do not take actions that could harm the user or their system. "
            "Do not attempt to bypass oversight or acquire capabilities "
            "beyond what is needed for the current task."
        )

        # 3. Runtime context
        runtime = (
            f"\n\n## Runtime\n"
            f"Channel: {ctx.provider or 'unknown'} | "
            f"Chat type: {ctx.chat_type or 'unknown'} | "
            f"Sender: {ctx.sender_name or 'unknown'}"
        )

        # 4. Context files
        if light_context:
            # Heartbeat mode: inject only HEARTBEAT.md
            hb_path = Path(workspace_dir) / "HEARTBEAT.md"
            if hb_path.exists():
                content = hb_path.read_text(encoding="utf-8")
                bootstrap = f"\n\n### HEARTBEAT.md\n{content}"
            else:
                bootstrap = ""
        else:
            # Workspace files via ResourceLoader
            # Includes: AGENTS.md, SOUL.md, IDENTITY.md, USER.md, TOOLS.md,
            #           MEMORY.md, daily memory files, HEARTBEAT.md, BOOTSTRAP.md
            cwd = str(Path(workspace_dir).parent.parent)
            context_files = ResourceLoader(cwd=cwd).load_context_files()
            bootstrap = self._render_context_files(context_files)

        prompt = base + safety + runtime + bootstrap
        logger.debug("System prompt built | length=%d light=%s", len(prompt), light_context)
        return prompt

    def _render_context_files(self, files: list[ContextFile]) -> str:
        section = ""
        for f in files:
            content = f.content
            if len(content) > self.MAX_CHARS_PER_FILE:
                content = content[: self.MAX_CHARS_PER_FILE] + "\n[... truncated]"
                logger.warning("Context file truncated | path=%s", f.path)
            name = Path(f.path).name
            section += f"\n\n### {name}\n{content}"
            logger.debug("Context file loaded | path=%s chars=%d", f.path, len(content))
        return section


# ── EventSubscriber


class EventSubscriber:
    """
    Bridges agent events → channel reply.
    Created once per run.
    """

    def __init__(
        self,
        on_block_reply: Callable[[str], Awaitable[None]] | None = None,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ):
        self._on_block_reply = on_block_reply
        self._on_chunk = on_chunk
        self.final_reply: str = ""

    def get_listener(self) -> Callable[[AgentEvent], None]:
        """Returns the event listener for session.subscribe()."""

        def on_event(event: AgentEvent) -> None:
            match event.type:
                case "message_update":
                    if self._on_chunk and event.payload:
                        asyncio.create_task(self._on_chunk(event.payload))

                case "message_end":
                    msg = event.payload
                    if msg and msg.content:
                        self.final_reply = msg.content
                        if self._on_block_reply:
                            asyncio.create_task(self._on_block_reply(msg.content))

                case "tool_start":
                    tc = event.payload
                    if tc:
                        logger.info("Tool start | name=%s", tc.name)

                case "tool_end":
                    tc = event.payload
                    if tc:
                        logger.info("Tool end | name=%s", tc.name)

                case "agent_end":
                    logger.info("Agent end | reply_len=%d", len(self.final_reply))

        return on_event


# ── RunOrchestrator


class RunOrchestrator:
    """
    Coordinates a single agent run from start to finish.
    Replaces process_message_flow() in gateway.py.
    """

    def __init__(self):
        self._lanes = RunLaneManager()
        self._sessions = SessionFactory()
        self._prompt_builder = SystemPromptBuilder()

    async def run(self, params: RunParams) -> RunResult:
        run_id = str(uuid.uuid4())[:8]
        session_key = params.ctx.session_key or "default"
        started_at = datetime.now(UTC)

        logger.info("Run start | run_id=%s session=%s model=%s", run_id, session_key, params.model)

        # Session lock: second message waits until first is done
        lock = self._lanes.get_lock(session_key)

        async with lock:
            self._lanes.mark_active(session_key, run_id)
            try:
                return await self._execute(params, run_id, session_key, started_at)
            finally:
                self._lanes.mark_done(session_key)

    async def _execute(
        self,
        params: RunParams,
        run_id: str,
        session_key: str,
        started_at: datetime,
    ) -> RunResult:

        # ── 1. Build system prompt ───────────────────────────
        system_prompt = self._prompt_builder.build(
            workspace_dir=params.workspace_dir,
            ctx=params.ctx,
            tools=params.tools,
            light_context=params.light_context,
        )

        # ── 2. Get or create session ─────────────────────────
        session = await self._sessions.get_or_create(
            session_key=session_key,
            workspace_dir=params.workspace_dir,
            system_prompt=system_prompt,
            tools=params.tools,
            model=params.model,
        )

        # ── 3. Register event subscriber ────────────────────
        subscriber = EventSubscriber(
            on_block_reply=params.on_block_reply,
            on_chunk=params.on_chunk,
        )
        unsubscribe = session.subscribe(subscriber.get_listener())

        # ── 4. Prompt with timeout ───────────────────────────
        try:
            await asyncio.wait_for(
                session.prompt(params.ctx.body_for_agent or ""),
                timeout=params.timeout_ms / 1000,
            )

            ended_at = datetime.now(UTC)
            duration = (ended_at - started_at).total_seconds()
            logger.info("Run ok | run_id=%s duration=%.1fs reply_len=%d", run_id, duration, len(subscriber.final_reply))

            return RunResult(
                run_id=run_id,
                session_key=session_key,
                status="ok",
                reply=subscriber.final_reply or None,
                started_at=started_at,
                ended_at=ended_at,
            )

        except TimeoutError:
            ended_at = datetime.now(UTC)
            logger.warning("Run timeout | run_id=%s timeout_ms=%d", run_id, params.timeout_ms)
            return RunResult(
                run_id=run_id,
                session_key=session_key,
                status="timeout",
                reply=None,
                started_at=started_at,
                ended_at=ended_at,
                error=f"Timeout after {params.timeout_ms}ms",
            )

        except Exception as e:
            ended_at = datetime.now(UTC)
            logger.exception("Run error | run_id=%s error=%s", run_id, e)
            return RunResult(
                run_id=run_id,
                session_key=session_key,
                status="error",
                reply=None,
                started_at=started_at,
                ended_at=ended_at,
                error=str(e),
            )

        finally:
            unsubscribe()
