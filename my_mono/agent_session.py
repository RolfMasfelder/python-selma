
# ============================================================
# my_mono/agent_session.py
# ============================================================

from __future__ import annotations
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Literal, Callable
from datetime import datetime, timezone

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from my_mono.pydantic_agent import (
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

from my_mono.resource_loader import ResourceLoader
from my_mono.system_prompt import build_system_prompt, BuildSystemPromptOptions

logger = logging.getLogger(__name__)


# ─── SESSION EINTRÄGE (JSONL) ────────────────────────────────

class SessionEntryBase(BaseModel):
    """Basisklasse für alle JSONL-Einträge."""
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
    tool_calls: list[dict] | None = None   # serialisierte ToolCallRequests
    tool_call_id: str | None = None        # nur bei role="tool"


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
    Verwaltet die JSONL-Datei auf Disk (append-only).
    Der Baum entsteht durch id/parent_id-Verweise.
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
        """Erstellt eine neue Session-Datei im .my_mono/ Verzeichnis."""
        session_dir = Path(cwd) / ".my_mono" / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        session_id = str(__import__("uuid").uuid4())[:8]
        session_file = session_dir / f"{session_id}.jsonl"
        return cls(session_file=session_file)

    @classmethod
    def in_memory(cls) -> "SessionManager":
        """Kein Disk-I/O — für Tests und Einmal-Nutzung."""
        return cls(session_file=None)

    # ── Schreiben ───────────────────────────────────────────

    def append_entry(self, entry: AnyEntry) -> AnyEntry:
        """Hängt einen Eintrag an — setzt id/parent_id automatisch."""
        entry.parent_id = self._leaf_id
        self._leaf_id = entry.id
        self._entries.append(entry)

        if self.session_file:
            with self.session_file.open("a", encoding="utf-8") as f:
                f.write(entry.model_dump_json() + "\n")

        logger.debug("Session entry | type=%s id=%s", entry.type, entry.id)
        return entry

    # ── Lesen ───────────────────────────────────────────────

    def build_context(self) -> list[AgentMessage]:
        """
        Rekonstruiert die LLM-relevanten Messages aus dem Baum.
        Stoppt beim letzten Compaction-Eintrag — ältere Messages werden
        durch den Summary ersetzt.
        """
        branch = self._get_branch()

        # Letzten Compaction-Index finden
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

    # ── Intern ──────────────────────────────────────────────

    def _get_branch(self) -> list[AnyEntry]:
        """Läuft von leaf_id zur Wurzel — gibt aktiven Pfad zurück."""
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
        """Lädt eine bestehende JSONL-Datei."""
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
    auto_compact_threshold: int = 100_000   # Token-Schätzwert
    ollama_base_url: str = "http://localhost:11434/v1"


class SettingsManager:
    """
    Merged global (~/.my_mono/settings.json) und
    projekt-lokal (.my_mono/settings.json).
    Projekt überschreibt global.
    """

    def __init__(self, cwd: str | Path = "."):
        self._global = self._load(Path.home() / ".my_mono" / "settings.json")
        self._project = self._load(Path(cwd) / ".my_mono" / "settings.json")

    @classmethod
    def in_memory(cls, overrides: dict = {}) -> "SettingsManager":
        mgr = cls.__new__(cls)
        mgr._global = Settings()
        mgr._project = Settings(**overrides)
        return mgr

    def get(self) -> Settings:
        merged = {**self._global.model_dump(), **self._project.model_dump()}
        return Settings(**merged)

    def _load(self, path: Path) -> Settings:
        if path.exists():
            return Settings(**json.loads(path.read_text()))
        return Settings()


# ─── MODEL REGISTRY ──────────────────────────────────────────

class ModelInfo(BaseModel):
    name: str
    size: int = 0
    modified_at: str = ""


class ModelRegistry:
    """
    Fragt Ollama nach verfügbaren Modellen via /api/tags (httpx).
    """

    def __init__(self, base_url: str = "http://localhost:11434"):
        self._base_url = base_url
        self._models: list[ModelInfo] = []

    async def refresh(self) -> list[ModelInfo]:
        """Aktuelle Modell-Liste von Ollama laden."""
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
    Wrapper um Agent.
    Verbindet: Agent-Loop + Session-Persistenz + Compaction.
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

        # DER entscheidende Schritt:
        # Agent-Core weiß nichts von Sessions/Compaction.
        # _build_context wird als Hook reingereicht — ab jetzt
        # greift AgentSession bei jedem Turn ein, ohne den Loop zu ändern.
        self._agent._options.convert_to_llm = self._build_context

        # Agent-Events in Session persistieren
        self._agent.subscribe(self._on_agent_event)

        logger.info("AgentSession initialized | session_id=%s",
                    session_manager.get_session_id())

    # ── Public API ───────────────────────────────────────────

    async def prompt(self, text: str) -> None:
        """Schickt User-Nachricht an den Agent und wartet auf Completion."""
        if self._agent.state.is_streaming:
            raise RuntimeError("Agent läuft bereits")

        msg = UserMessage(content=text)
        self._persist_message(msg)

        task = self._agent.prompt(msg)
        await task

    def subscribe(self, listener: Callable[[AgentEvent], None]) -> Callable:
        return self._agent.subscribe(listener)

    # ── Modell-Kontrolle ─────────────────────────────────────

    async def set_model(self, model: str) -> None:
        """Wechselt das Modell und schreibt Eintrag in JSONL."""
        self._agent.state.model = model
        self._session_manager.append_entry(ModelChangeEntry(model=model))
        logger.info("Model changed | model=%s", model)

    def set_thinking_level(self, level) -> None:
        self._agent.state.thinking_level = level
        logger.info("Thinking level changed | level=%s", level)

    # ── Session-Management ───────────────────────────────────

    async def new_session(self) -> None:
        """Startet eine neue leere Session (neues JSONL)."""
        self._session_manager = SessionManager.create()
        self._agent.state.messages.clear()
        self._write_session_meta()
        logger.info("New session started | session_id=%s",
                    self._session_manager.get_session_id())

    async def resume_session(self, session_file: Path) -> None:
        """Lädt eine gespeicherte Session und rekonstruiert den Kontext."""
        self._session_manager = SessionManager(session_file=session_file)
        self._agent.state.messages = self._session_manager.build_context()
        logger.info("Session resumed | file=%s messages=%d",
                    session_file, len(self._agent.state.messages))

    # ── Kompaktierung ────────────────────────────────────────

    async def compact(self, instructions: str = "") -> None:
        """
        Fasst den bisherigen Kontext zusammen.
        Danach bleibt nur der Summary im Live-Kontext.

        1. Gesamter Kontext → LLM → Summary
        2. Summary als "compaction"-Eintrag in JSONL
        3. Agent-State: nur noch der Summary
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
        openai_messages = self._agent._to_openai_messages(context)
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

    # ── convert_to_llm Hook ──────────────────────────────────

    def _build_context(self, messages: list[AgentMessage]) -> list[AgentMessage]:
        """
        Wird von Agent._run_loop() bei JEDEM Turn aufgerufen.
        Hier greift AgentSession in den Loop ein — ohne ihn zu ändern.

        messages kommt direkt vom Agent-Loop — enthält alle aktuellen
        Messages inklusive Tool-Results die noch nicht persistiert sind.
        """
        settings = self._settings_manager.get()

        # 1. Live-Messages aus dem Agent-Loop verwenden
        #    (nicht SessionManager — der kann Tool-Results noch nicht kennen)
        base_messages = list(messages)

        # 2. Bilder filtern wenn in Settings deaktiviert
        if settings.block_images:
            base_messages = self._strip_images(base_messages)

        # 3. Auto-Compaction: Token-Schätzwert überschritten?
        token_estimate = sum(len(str(m)) for m in base_messages)
        if token_estimate > settings.auto_compact_threshold:
            logger.info("Auto-compaction triggered | tokens_est=%d threshold=%d",
                        token_estimate, settings.auto_compact_threshold)
            asyncio.create_task(self.compact())

        return base_messages

    # ── Persistenz ───────────────────────────────────────────

    def _on_agent_event(self, event: AgentEvent) -> None:
        """Schreibt Agent-Events als Messages in die JSONL."""
        if event.type == "message_end":
            msg: AssistantMessage = event.payload
            self._persist_message(msg)

    def _persist_message(self, msg: AgentMessage) -> None:
        """Jede Nachricht sofort in JSONL schreiben."""
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
        """Bilder aus Messages entfernen (Platzhalter für multimodale Modelle)."""
        return messages  # TODO: implementieren wenn multimodal nötig

    # ── Properties ───────────────────────────────────────────

    @property
    def state(self) -> AgentState:
        """Direktzugriff auf den Agent-State — wie session.state in pi-mono."""
        return self._agent.state

    @property
    def agent(self) -> Agent:
        """Escape-Hatch für Low-Level-Zugriff auf den Agent-Core."""
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
    in_memory: bool = False
    continue_session: Path | None = None

    model_config = {"arbitrary_types_allowed": True}


async def create_agent_session(options: CreateSessionOptions) -> AgentSession:
    """
    Die eine Factory-Funktion. Baut alles zusammen.

    create_agent_session()
    │
    ├─ 1. ModelRegistry    → verfügbare Ollama-Modelle laden
    ├─ 2. SessionManager   → neue JSONL oder bestehende laden
    ├─ 3. SettingsManager  → global + projekt-lokal merged
    ├─ 4. Modell auflösen  → Option > erstes verfügbares > Fehler
    ├─ 5. ResourceLoader   → globale AGENTS.md laden
    ├─ 6. System Prompt    → build_system_prompt() mit context_files
    ├─ 7. Agent bauen      → nackter Loop, noch kein Context-Hook
    ├─ 8. History laden    → bei continue_session: Baum → Messages
    └─ 9. AgentSession     → überschreibt convert_to_llm → alles verdrahtet
    """

    # ── 1. Model Registry ───────────────────────────────────
    base_url_root = options.ollama_base_url.replace("/v1", "")
    model_registry = ModelRegistry(base_url=base_url_root)
    try:
        await model_registry.refresh()
    except Exception as e:
        logger.warning("ModelRegistry refresh failed: %s", e)

    # ── 2. Session Manager ──────────────────────────────────
    if options.continue_session:
        session_manager = SessionManager(session_file=options.continue_session)
    elif options.in_memory:
        session_manager = SessionManager.in_memory()
    else:
        session_manager = SessionManager.create(cwd=options.cwd)

    # ── 3. Settings ─────────────────────────────────────────
    settings_manager = SettingsManager(cwd=options.cwd)

    # ── 4. Modell auflösen ──────────────────────────────────
    model = options.model
    if not model:
        available = model_registry.get_available()
        if not available:
            raise RuntimeError("Kein Ollama-Modell verfügbar. Ist Ollama gestartet?")
        model = available[0].name
        logger.info("No model specified — using first available | model=%s", model)

    # ── 5. Resources laden ──────────────────────────────────────
    resource_loader = ResourceLoader(cwd=options.cwd or ".")
    context_files = resource_loader.load_context_files()

    # ── 6. System Prompt bauen ──────────────────────────────────
    system_prompt = options.system_prompt or build_system_prompt(
        BuildSystemPromptOptions(
            cwd=options.cwd,
            context_files=context_files,
        )
    )
    logger.debug("System prompt built | length=%d context_files=%d",
                len(system_prompt), len(context_files))
    logger.debug("System prompt content:\n%s", system_prompt)

    # ── 7. Agent bauen ──────────────────────────────────────
    # convert_to_llm ist Platzhalter — wird von AgentSession überschrieben
    agent = Agent(AgentOptions(
        model=model,
        tools=options.tools,
        system_prompt=system_prompt,
        thinking_level=options.thinking_level,
        ollama_base_url=options.ollama_base_url,
        convert_to_llm=lambda msgs: msgs,  # ← Platzhalter
    ))

    # ── 8. History laden ────────────────────────────────────
    if options.continue_session:
        agent.state.messages = session_manager.build_context()
        logger.info("History restored | messages=%d", len(agent.state.messages))
    else:
        session_manager.append_entry(SessionMetaEntry(
            model=model,
            system_prompt=system_prompt,
        ))

    # ── 9. AgentSession zusammensetzen ──────────────────────
    # __init__ überschreibt agent.convert_to_llm → _build_context
    session = AgentSession(
        agent=agent,
        session_manager=session_manager,
        settings_manager=settings_manager,
        model_registry=model_registry,
    )

    logger.info("AgentSession ready | model=%s session_id=%s in_memory=%s",
                model, session.session_id, options.in_memory)

    return session