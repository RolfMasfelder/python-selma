# ============================================================
# my_mono/agent_session.py
# ============================================================

from __future__ import annotations
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Literal, Callable
from datetime import datetime, timezone

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from openai.types.chat import ChatCompletionMessageParam
from my_mono.agent import (
    Agent,
    AgentOptions,
    AgentTool,
    AgentEvent,
    AgentMessage,
    AgentState,
    UserMessage,
    AssistantMessage,
    ToolResultMessage,
)

from my_mono.tools import create_coding_tools
from my_mono.resource_loader import ResourceLoader
from my_mono.system_prompt import build_system_prompt, BuildSystemPromptOptions, CODING_TOOLS

logger = logging.getLogger(__name__)


# ─── SESSION ENTRIES (JSONL) ─────────────────────────────────

class SessionEntryBase(BaseModel):
    """Base class for all JSONL entries."""
    id: str = Field(default_factory=lambda: str(__import__("uuid").uuid4()))
    parent_id: str | None = None
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class SessionMetaEntry(SessionEntryBase):
    type: Literal["session"] = "session"
    model: str
    system_prompt: str


class MessageEntry(SessionEntryBase):
    type: Literal["message"] = "message"
    role: Literal["user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[dict] | None = None   # serialized ToolCallRequests
    tool_call_id: str | None = None        # only for role="tool"


class CompactionEntry(SessionEntryBase):
    type: Literal["compaction"] = "compaction"
    summary: str
    tokens_before: int = 0


class ModelChangeEntry(SessionEntryBase):
    type: Literal["model_change"] = "model_change"
    model: str


AnyEntry = (
    SessionMetaEntry
    | MessageEntry
    | CompactionEntry
    | ModelChangeEntry
)


# ─── SESSION MANAGER ─────────────────────────────────────────

class SessionManager:
    """
    Manages the JSONL file on disk (append-only).
    The tree is formed via id/parent_id references.
    """

    def __init__(self, session_file: Path | None = None):
        self._entries: list[AnyEntry] = []
        self._leaf_id: str | None = None
        self.session_file = session_file

        if session_file and session_file.exists():
            self._load(session_file)
            logger.info("Session loaded | file=%s entries=%d",
                        session_file, len(self._entries))
        else:
            logger.info("Session started in-memory" if not session_file
                        else f"Session started | file={session_file}")

    # ── Factory ─────────────────────────────────────────────

    @classmethod
    def create(cls, cwd: str | Path = ".") -> "SessionManager":
        """Creates a new session file in the .my_mono/ directory."""
        session_dir = Path(cwd) / ".my_mono" / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        session_id = str(__import__("uuid").uuid4())[:8]
        session_file = session_dir / f"{session_id}.jsonl"
        return cls(session_file=session_file)

    @classmethod
    def open(cls, session_file: str | Path) -> "SessionManager":
        """Opens an existing session file."""
        return cls(session_file=Path(session_file))

    @classmethod
    def continue_recent(cls, cwd: str | Path = ".") -> "SessionManager":
        """
        Continues the most recent session in <cwd>/.my_mono/sessions/.
        Creates a new session if none exists.
        """
        session_dir = Path(cwd) / ".my_mono" / "sessions"
        files = sorted(
            session_dir.glob("*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ) if session_dir.exists() else []

        if files:
            logger.info("Continuing recent session | file=%s", files[0])
            return cls(session_file=files[0])

        logger.info("No recent session found — creating new one")
        return cls.create(cwd=cwd)

    # ── Write ────────────────────────────────────────────────

    def append_entry(self, entry: AnyEntry) -> AnyEntry:
        """Appends an entry — sets id/parent_id automatically."""
        entry.parent_id = self._leaf_id
        self._leaf_id = entry.id
        self._entries.append(entry)

        if self.session_file:
            with self.session_file.open("a", encoding="utf-8") as f:
                f.write(entry.model_dump_json() + "\n")

        logger.debug("Session entry | type=%s id=%s", entry.type, entry.id)
        return entry

    # ── Read ─────────────────────────────────────────────────

    def build_context(self) -> list[AgentMessage]:
        """
        Reconstructs the LLM-relevant messages from the tree.
        Stops at the last compaction entry — older messages are
        replaced by the summary.
        """
        branch = self._get_branch()

        # Find the last compaction index
        last_compact = max(
            (i for i, e in enumerate(branch) if e.type == "compaction"),
            default=0
        )

        messages: list[AgentMessage] = []
        for entry in branch[last_compact:]:
            if entry.type == "compaction":
                messages.append(UserMessage(content=f"[Summary]: {entry.summary}"))

            elif entry.type == "message":
                if entry.role == "user":
                    messages.append(UserMessage(content=entry.content or ""))

                elif entry.role == "assistant":
                    tool_calls = None
                    if entry.tool_calls:
                        from my_mono.agent import ToolCallRequest
                        tool_calls = [ToolCallRequest(**tc) for tc in entry.tool_calls]
                    messages.append(AssistantMessage(
                        content=entry.content,
                        tool_calls=tool_calls,
                    ))

                elif entry.role == "tool":
                    messages.append(ToolResultMessage(
                        tool_call_id=entry.tool_call_id or "",
                        content=entry.content or "",
                    ))

        logger.debug("Context built | messages=%d (from %d entries)",
                     len(messages), len(branch) - last_compact)
        return messages

    def get_session_id(self) -> str | None:
        meta = next((e for e in self._entries if e.type == "session"), None)
        return meta.id if meta else None

    # ── Internal ─────────────────────────────────────────────

    def _get_branch(self) -> list[AnyEntry]:
        """Walks from leaf_id to the root — returns the active path."""
        if not self._leaf_id:
            return []
        by_id = {e.id: e for e in self._entries}
        path: list[AnyEntry] = []
        current_id: str | None = self._leaf_id
        while current_id and current_id in by_id:
            entry = by_id[current_id]
            path.insert(0, entry)
            current_id = entry.parent_id
        return path

    def _load(self, path: Path) -> None:
        """Loads an existing JSONL file."""
        type_map = {
            "session": SessionMetaEntry,
            "message": MessageEntry,
            "compaction": CompactionEntry,
            "model_change": ModelChangeEntry,
        }
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            entry_cls = type_map.get(raw.get("type"))
            if entry_cls:
                entry = entry_cls(**raw)
                self._entries.append(entry)
                self._leaf_id = entry.id


# ─── SETTINGS ────────────────────────────────────────────────

class Settings(BaseModel):
    block_images: bool = False
    auto_compact_threshold: int = 100_000   # token estimate
    ollama_base_url: str = "http://localhost:11434/v1"


class SettingsManager:
    """
    Loads settings from a single file: <cwd>/.my_mono/settings.json.
    Falls back to defaults if the file does not exist.
    """

    def __init__(self, cwd: str | Path = "."):
        path = Path(cwd) / ".my_mono" / "settings.json"
        if path.exists():
            self._settings = Settings(**json.loads(path.read_text()))
            logger.debug("Settings loaded | path=%s", path)
        else:
            self._settings = Settings()

    @classmethod
    def in_memory(cls, overrides: dict = {}) -> "SettingsManager":
        mgr = cls.__new__(cls)
        mgr._settings = Settings(**overrides)
        return mgr

    def get(self) -> Settings:
        return self._settings


# ─── MODEL REGISTRY ──────────────────────────────────────────

class ModelInfo(BaseModel):
    name: str
    size: int = 0
    modified_at: str = ""


class ModelRegistry:
    """
    Queries Ollama for available models via /api/tags (httpx).
    """

    def __init__(self, base_url: str = "http://localhost:11434"):
        self._base_url = base_url
        self._models: list[ModelInfo] = []

    async def refresh(self) -> list[ModelInfo]:
        """Fetches the current model list from Ollama."""
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{self._base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            self._models = [ModelInfo(**m) for m in data.get("models", [])]
            logger.info("ModelRegistry refreshed | models=%s",
                        [m.name for m in self._models])
            return self._models

    def get_available(self) -> list[ModelInfo]:
        return self._models

    def find(self, name: str) -> ModelInfo | None:
        return next((m for m in self._models if m.name == name), None)


# ─── AGENT SESSION ───────────────────────────────────────────

class AgentSession:
    """
    Wrapper around Agent.
    Connects: agent loop + session persistence + compaction.
    """

    def __init__(
        self,
        agent: Agent,
        session_manager: SessionManager,
        settings_manager: SettingsManager,
        model_registry: ModelRegistry,
    ):
        self._agent = agent
        self._session_manager = session_manager
        self._settings_manager = settings_manager
        self._model_registry = model_registry

        # THE key step:
        # The agent core knows nothing about sessions/compaction.
        # _build_context is injected as a hook — from now on
        # AgentSession intervenes on every turn without modifying the loop.
        self._agent._options.convert_to_llm = self._build_context

        # Persist agent events into the session
        self._agent.subscribe(self._on_agent_event)

        logger.info("AgentSession initialized | session_id=%s",
                    session_manager.get_session_id())

    # ── Public API ───────────────────────────────────────────

    async def prompt(self, text: str) -> None:
        """Sends a user message to the agent and waits for completion."""
        if self._agent.state.is_streaming:
            raise RuntimeError("Agent is already running")

        msg = UserMessage(content=text)
        self._persist_message(msg)

        task = self._agent.prompt(msg)
        await task

    def subscribe(self, listener: Callable[[AgentEvent], None]) -> Callable:
        return self._agent.subscribe(listener)

    # ── Model Control ────────────────────────────────────────

    async def set_model(self, model: str) -> None:
        """Switches the model and writes an entry to JSONL."""
        self._agent.state.model = model
        self._session_manager.append_entry(ModelChangeEntry(model=model))
        logger.info("Model changed | model=%s", model)

    def set_thinking_level(self, level) -> None:
        self._agent.state.thinking_level = level
        logger.info("Thinking level changed | level=%s", level)

    # ── Session Management ───────────────────────────────────

    async def new_session(self) -> None:
        """Starts a new empty session (new JSONL file)."""
        self._session_manager = SessionManager.create()
        self._agent.state.messages.clear()
        self._write_session_meta()
        logger.info("New session started | session_id=%s",
                    self._session_manager.get_session_id())

    async def resume_session(self, session_file: Path) -> None:
        """Loads a saved session and reconstructs the context."""
        self._session_manager = SessionManager(session_file=session_file)
        self._agent.state.messages = self._session_manager.build_context()
        logger.info("Session resumed | file=%s messages=%d",
                    session_file, len(self._agent.state.messages))

    # ── Compaction ───────────────────────────────────────────

    async def compact(self, instructions: str = "") -> None:
        """
        Summarizes the current context.
        Afterwards only the summary remains in the live context.

        1. Full context → LLM → summary
        2. Summary as "compaction" entry in JSONL
        3. Agent state: only the summary remains
        """
        context = self._session_manager.build_context()
        token_estimate = sum(len(str(m)) for m in context)

        logger.info("Compaction start | messages=%d tokens_est=%d",
                    len(context), token_estimate)

        extra = f"\nExtra instructions: {instructions}" if instructions else ""
        summary_prompt = (
            "Summarize the conversation so far as concisely as possible, "
            "preserving all important facts, decisions and context."
            + extra
        )

        client = AsyncOpenAI(
            base_url=self._agent._options.ollama_base_url,
            api_key="ollama",
        )
        openai_messages = self._to_openai_messages(context)
        openai_messages.append({"role": "user", "content": summary_prompt})

        response = await client.chat.completions.create(
            model=self._agent.state.model,
            messages=openai_messages,
            stream=False,
        )
        summary = response.choices[0].message.content or ""

        self._session_manager.append_entry(CompactionEntry(
            summary=summary,
            tokens_before=token_estimate,
        ))

        self._agent.state.messages = [UserMessage(content=f"[Summary]: {summary}")]

        logger.info("Compaction done | summary_len=%d", len(summary))

    # ── OpenAI Format Conversion ────────────────────────────

    def _to_openai_messages(
        self, messages: list[AgentMessage]
    ) -> list[ChatCompletionMessageParam]:
        """
        Convert internal AgentMessages to the OpenAI messages format.
        Includes the system prompt as the first message.
        Used by compact() to build the summarisation request.
        """
        result: list[ChatCompletionMessageParam] = []

        if self._agent._options.system_prompt:
            result.append({"role": "system",
                           "content": self._agent._options.system_prompt})

        for msg in messages:
            if isinstance(msg, UserMessage):
                result.append({"role": "user", "content": msg.content})

            elif isinstance(msg, AssistantMessage):
                entry: dict = {"role": "assistant", "content": msg.content or ""}
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                result.append(entry)

            elif isinstance(msg, ToolResultMessage):
                result.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content,
                })

        return result

    # ── convert_to_llm Hook ──────────────────────────────────

    def _build_context(self, messages: list[AgentMessage]) -> list[AgentMessage]:
        """
        Called by Agent._run_loop() on EVERY turn.
        This is where AgentSession hooks into the loop — without modifying it.

        messages comes directly from the agent loop — contains all current
        messages including tool results that have not yet been persisted.
        """
        settings = self._settings_manager.get()

        # 1. Use live messages from the agent loop
        #    (not SessionManager — it may not know about tool results yet)
        base_messages = list(messages)

        # 2. Filter images if disabled in settings
        if settings.block_images:
            base_messages = self._strip_images(base_messages)

        # 3. Auto-compaction: token estimate exceeded?
        token_estimate = sum(len(str(m)) for m in base_messages)
        if token_estimate > settings.auto_compact_threshold:
            logger.info("Auto-compaction triggered | tokens_est=%d threshold=%d",
                        token_estimate, settings.auto_compact_threshold)
            asyncio.create_task(self.compact())

        return base_messages

    # ── Persistence ──────────────────────────────────────────

    def _on_agent_event(self, event: AgentEvent) -> None:
        """Writes agent events as messages to the JSONL file."""
        if event.type == "message_end":
            msg: AssistantMessage = event.payload
            self._persist_message(msg)

    def _persist_message(self, msg: AgentMessage) -> None:
        """Writes each message immediately to JSONL."""
        if isinstance(msg, UserMessage):
            self._session_manager.append_entry(MessageEntry(
                role="user",
                content=msg.content,
            ))
        elif isinstance(msg, AssistantMessage):
            self._session_manager.append_entry(MessageEntry(
                role="assistant",
                content=msg.content,
                tool_calls=[tc.model_dump() for tc in msg.tool_calls]
                           if msg.tool_calls else None,
            ))
        elif isinstance(msg, ToolResultMessage):
            self._session_manager.append_entry(MessageEntry(
                role="tool",
                tool_call_id=msg.tool_call_id,
                content=msg.content,
            ))

    def _write_session_meta(self) -> None:
        self._session_manager.append_entry(SessionMetaEntry(
            model=self._agent.state.model,
            system_prompt=self._agent._options.system_prompt,
        ))

    def _strip_images(self, messages: list[AgentMessage]) -> list[AgentMessage]:
        """Removes images from messages (placeholder for multimodal models)."""
        return messages  # TODO: implement when multimodal support is needed

    # ── Properties ───────────────────────────────────────────

    @property
    def state(self) -> AgentState:
        """Direct access to the agent state — like session.state in pi-mono."""
        return self._agent.state

    @property
    def agent(self) -> Agent:
        """Escape hatch for low-level access to the agent core."""
        return self._agent

    @property
    def session_file(self) -> Path | None:
        return self._session_manager.session_file

    @property
    def session_id(self) -> str | None:
        return self._session_manager.get_session_id()

    @property
    def is_streaming(self) -> bool:
        return self._agent.state.is_streaming


# ─── FACTORY FUNCTION ────────────────────────────────────────

class CreateSessionOptions(BaseModel):
    model: str = "llama3.2"
    system_prompt: str | None = None 
    tools: list[AgentTool] = Field(default_factory=list)
    thinking_level: Literal["low", "medium", "high"] = "low"
    ollama_base_url: str = "http://localhost:11434/v1"
    cwd: str = "."
    session_manager: SessionManager | None = None
    continue_session: Path | None = None

    model_config = {"arbitrary_types_allowed": True}


async def create_agent_session(options: CreateSessionOptions) -> AgentSession:
    """
    The single factory function. Assembles everything.

    create_agent_session()
    │
    ├─ 1. ModelRegistry    → load available Ollama models
    ├─ 2. SessionManager   → create new JSONL or load existing
    ├─ 3. SettingsManager  → merge global + project-local
    ├─ 4. Resolve model    → option > first available > error
    ├─ 5. ResourceLoader   → load global AGENTS.md
    ├─ 6. Instantiate tools → create_coding_tools(cwd) or caller-supplied
    ├─ 7. Build system prompt → tool names + context_files
    ├─ 8. Build agent      → bare loop, no context hook yet
    ├─ 9. Load history     → on continue_session: tree → messages
    └─ 10. AgentSession    → overrides convert_to_llm → everything wired up
    """

    # ── 1. Model Registry ───────────────────────────────────
    base_url_root = options.ollama_base_url.replace("/v1", "")
    model_registry = ModelRegistry(base_url=base_url_root)
    try:
        await model_registry.refresh()
    except Exception as e:
        logger.warning("ModelRegistry refresh failed: %s", e)

    # ── 2. Session Manager ──────────────────────────────────
    if options.session_manager is not None:
        session_manager = options.session_manager
    elif options.continue_session:
        session_manager = SessionManager(session_file=options.continue_session)
    else:
        session_manager = SessionManager.create(cwd=options.cwd)

    # ── 3. Settings ─────────────────────────────────────────
    settings_manager = SettingsManager(cwd=options.cwd)

    # ── 4. Resolve model ────────────────────────────────────
    model = options.model
    if not model:
        available = model_registry.get_available()
        if not available:
            raise RuntimeError("No Ollama model available. Is Ollama running?")
        model = available[0].name
        logger.info("No model specified — using first available | model=%s", model)

    # ── 5. Load resources ───────────────────────────────────
    resource_loader = ResourceLoader(cwd=options.cwd or ".")
    context_files = resource_loader.load_context_files()

    # ── 6. Instantiate tools ────────────────────────────────
    # Use caller-supplied tools, or fall back to the four standard coding tools
    # (read, bash, edit, write) bound to the correct cwd.
    tools = options.tools if options.tools else create_coding_tools(
        options.cwd or os.getcwd()
    )
    logger.debug("Tools loaded | names=%s", [t.name for t in tools])

    # ── 7. Build system prompt ──────────────────────────────
    # When tools were auto-created, reflect the actual tool names in the prompt.
    if options.system_prompt:
        system_prompt = options.system_prompt
    else:
        system_prompt = build_system_prompt(
            BuildSystemPromptOptions(
                cwd=options.cwd,
                context_files=context_files,
                selected_tools=[t.name for t in tools],
                # Pass actual descriptions so custom tools appear correctly,
                # not just the built-in TOOL_DESCRIPTIONS registry.
                tool_descriptions={t.name: t.description for t in tools},
            )
        )
    logger.debug("System prompt built | length=%d context_files=%d",
                len(system_prompt), len(context_files))
    logger.debug("System prompt content:\n%s", system_prompt)

    # ── 8. Build agent ──────────────────────────────────────
    # convert_to_llm is a placeholder — will be overridden by AgentSession
    agent = Agent(AgentOptions(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        thinking_level=options.thinking_level,
        ollama_base_url=options.ollama_base_url,
        convert_to_llm=lambda msgs: msgs,  # ← placeholder
    ))

    # ── 9. Load history ─────────────────────────────────────
    # Restore history when opening an existing session (via session_manager
    # with a file, or legacy continue_session).
    has_existing = (
        options.continue_session is not None
        or (options.session_manager is not None
            and options.session_manager.session_file is not None
            and options.session_manager.session_file.exists()
            and options.session_manager.get_session_id() is not None)
    )
    if has_existing:
        agent.state.messages = session_manager.build_context()
        logger.info("History restored | messages=%d", len(agent.state.messages))
    else:
        session_manager.append_entry(SessionMetaEntry(
            model=model,
            system_prompt=system_prompt,
        ))

    # ── 10. Assemble AgentSession ────────────────────────────
    # __init__ overrides agent.convert_to_llm → _build_context
    session = AgentSession(
        agent=agent,
        session_manager=session_manager,
        settings_manager=settings_manager,
        model_registry=model_registry,
    )

    logger.info("AgentSession ready | model=%s session_id=%s",
                model, session.session_id)

    return session