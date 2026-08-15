# ============================================================
# command_manager.py
#
# Handles all slash commands from any channel.
# Injected into process_message_flow() in gateway.py — called
# after the allowlist check, before the agent is invoked.
# ============================================================

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from selma.compaction import compact_session
from selma.config import THINKING_LEVELS, SelmaConfig, get_default_model, resolve_thinking_default, resolve_tools_allow
from selma.data import NormalizedTurnInput
from selma.helper import get_workspace
from selma.runtime import DeliveryContext
from selma.runtime import memory_flush as _memory_flush_fn
from selma.session_store import load_session_store, reset_session, resolve_session_file, save_session_store
from selma.skills import _find_skill_files, _parse_frontmatter
from selma.tools import ALL_TOOL_NAMES, get_tool_descriptions

logger = logging.getLogger(__name__)

_THINKING_LEVELS = THINKING_LEVELS | {"off"}
_THINK_ALIASES = {"/think", "/thinking", "/t"}

# Default Ollama base URL — used by /models to query available models.
# Matches the default in CreateSessionOptions.
_OLLAMA_BASE_URL = "http://localhost:11434"


class CommandManager:
    """
    Parses and dispatches slash commands.

    Supported commands:
      /model [name]              — show or set the active model
      /models                    — list models available in Ollama
      /think <off|low|medium|high> — show or set the thinking level
      /thinking, /t              — aliases for /think
      /config show               — print the current selma.json
      /reset                     — reset the current session
      /new                       — alias for /reset
    """

    def __init__(self, config: SelmaConfig, cwd: str = "."):
        self._config = config
        self._cwd = cwd

    async def handle(self, ctx: NormalizedTurnInput, delivery: DeliveryContext | None = None) -> str:
        """Entry point — parses ctx.body_for_commands and dispatches."""
        text = (ctx.body_for_commands or "").strip()
        if not text.startswith("/"):
            return "Not a command."

        parts = text.split()
        cmd = parts[0].lower()
        args = parts[1:]

        match cmd:
            case "/model":
                return self._cmd_model(ctx, args)
            case "/models":
                return await self._cmd_models()
            case c if c in _THINK_ALIASES:
                return self._cmd_think(ctx, args)
            case "/config":
                return self._cmd_config(args)
            case "/reset" | "/new":
                return await self._cmd_reset(ctx)
            case "/help":
                return self._cmd_help()
            case "/tools":
                return self._cmd_tools()
            case "/allowlist":
                return self._cmd_allowlist()
            case "/status":
                return self._cmd_status(ctx)
            case "/commands":
                return self._cmd_commands()
            case "/compact":
                return await self._cmd_compact(ctx, delivery)
            case "/skills":
                return self._cmd_skills()
            case _:
                return f"Unknown command: `{cmd}`."

    # ── helpers ──────────────────────────────────────────────

    def _get_session_record(self, session_key: str):
        store = load_session_store(cwd=self._cwd)
        normalized = session_key.strip().lower()
        return store, store.sessions.get(normalized)

    # ── /model ───────────────────────────────────────────────

    def _cmd_model(self, ctx: NormalizedTurnInput, args: list[str]) -> str:
        session_key = ctx.session_key or "default"
        store, record = self._get_session_record(session_key)

        if not args:
            _, default_model = get_default_model(self._config)
            model = (record.model_override if record else None) or default_model
            return f"Current model: `{model}`"

        new_model = args[0]
        if record:
            record.model_override = new_model
            save_session_store(store)
            logger.info("Model changed via command | session=%s model=%s", session_key, new_model)
        return f"Model set to `{new_model}`."

    # ── /models ──────────────────────────────────────────────

    async def _cmd_models(self) -> str:
        from selma.my_mono.agent_session import list_ollama_models

        try:
            models = await list_ollama_models(_OLLAMA_BASE_URL)
            if not models:
                return "No models available."
            lines = [f"• `{m.name}`" for m in models]
            return "Available models:\n" + "\n".join(lines)
        except Exception as e:
            logger.warning("Model listing failed: %s", e)
            return f"Could not fetch models: {e}"

    # ── /think ───────────────────────────────────────────────

    def _cmd_think(self, ctx: NormalizedTurnInput, args: list[str]) -> str:
        session_key = ctx.session_key or "default"
        store, record = self._get_session_record(session_key)

        if not args:
            level = record.thinking_level if record else None
            return f"Current thinking level: `{level or 'off'}`"

        level_str = args[0].lower()
        if level_str not in _THINKING_LEVELS:
            return f"Invalid level `{level_str}`. Use: off, low, medium, high."
        level = None if level_str == "off" else level_str
        if record:
            record.thinking_level = level
            save_session_store(store)
            logger.info("Thinking level changed via command | session=%s level=%s", session_key, level_str)
        return f"Thinking level set to `{level_str}`."

    # ── /reset | /new ────────────────────────────────────────

    async def _cmd_reset(self, ctx: NormalizedTurnInput) -> str:
        session_key = ctx.session_key or "default"
        store, record = self._get_session_record(session_key)

        if record is None:
            return "No active session found."

        reset_session(store, record, cwd=self._cwd)
        logger.info("Session reset via command | session_key=%s", session_key)
        return "Session reset. Starting fresh."

    # ── /compact ─────────────────────────────────────────────

    async def _cmd_compact(self, ctx: NormalizedTurnInput, delivery: DeliveryContext | None = None) -> str:
        session_key = ctx.session_key or "default"
        store, record = self._get_session_record(session_key)
        if record is None:
            return "No active session found — nothing to compact."

        def _emit(name: str) -> None:
            if delivery and delivery.on_tool_call:
                delivery.on_tool_call(name, {})

        session_file = resolve_session_file(record, cwd=self._cwd)

        _emit("🔄 Saving memory")
        await asyncio.sleep(0)
        await _memory_flush_fn(session_key, cwd=self._cwd)

        _emit("🗜️ Compacting history")
        await asyncio.sleep(0)
        result = await compact_session(session_file=session_file, config=self._config)

        if not result.ok:
            return f"Compaction failed: {result.reason}"

        if not result.compacted:
            return f"Nothing compacted: {result.reason}"

        reduction = result.tokens_before - result.tokens_after
        pct = int(reduction / result.tokens_before * 100) if result.tokens_before else 0
        return f"Session compacted. Tokens: ~{result.tokens_before} → ~{result.tokens_after} (−{reduction}, −{pct}%)"

    async def _memory_flush(self, session_key: str) -> None:
        await _memory_flush_fn(session_key, cwd=self._cwd)

    # ── /commands ────────────────────────────────────────────

    def _cmd_commands(self) -> str:
        commands = [
            ("/model [name]", "Show or set the active model"),
            ("/models", "List all models available in Ollama"),
            ("/think <level>", "Show or set thinking level (off/low/medium/high)"),
            ("/reset", "Reset the current session (clears history)"),
            ("/new", "Alias for /reset"),
            ("/compact", "Compact the session context to save tokens"),
            ("/config show", "Show current selma.json"),
            ("/allowlist", "Show tool allowlist from selma.json"),
            ("/status", "Show runtime status (model, session, messages)"),
            ("/tools", "List active tools"),
            ("/commands", "Show this command list"),
            ("/skills", "List all available skills"),
            ("/help", "Show short help summary"),
            ("/healthcheck", "Run Selma system health check"),
            ("/skill <name> [input]", "Run a skill by name"),
        ]

        lines = ["**Available commands**", ""]
        for cmd, desc in commands:
            lines.append(f"`{cmd}` — {desc}")

        return "\n\n".join(lines)

    # ── /status ──────────────────────────────────────────────

    def _cmd_status(self, ctx: NormalizedTurnInput) -> str:
        session_key = ctx.session_key or "default"
        _, record = self._get_session_record(session_key)

        # Effective model / provider
        default_provider, default_model = get_default_model(self._config)
        provider = (record.provider_override if record else None) or default_provider
        model = (record.model_override if record else None) or default_model

        # Effective thinking level
        thinking = (
            (record.thinking_level if record else None)
            or resolve_thinking_default(self._config, provider, model)
            or "off"
        )

        # Message count from transcript file
        session_file = resolve_session_file(record, cwd=self._cwd) if record else None
        if session_file and Path(session_file).exists():
            msg_count = sum(1 for _ in Path(session_file).open(encoding="utf-8"))
        else:
            msg_count = "—"

        # Last interaction
        last = (record.last_interaction_at if record else None) or "—"

        # Heartbeat
        hb = self._config.heartbeat
        hb_every = hb.every if hb.every and hb.every != "0m" else "off"
        hb_target = hb.target if hb_every != "off" else "—"
        hb_extra = ""
        if hb_every != "off":
            flags = []
            if hb.light_context:
                flags.append("light")
            if hb.isolated_session:
                flags.append("isolated")
            if hb.active_hours:
                flags.append(f"active {hb.active_hours.start}–{hb.active_hours.end}")
            if flags:
                hb_extra = f"  ({', '.join(flags)})"

        # Next heartbeat time
        import selma.heartbeat as _hb_mod

        hb_next = _hb_mod.next_heartbeat_at
        hb_next_str = hb_next.strftime("%H:%M:%S") if hb_next and hb_every != "off" else "—"

        lines = [
            "**Selma Status**",
            "",
            f"`provider`   {provider}",
            f"`model`      {model}",
            f"`thinking`   {thinking}",
            "",
            f"`session`    {session_key}",
            f"`session_id` {record.session_id if record else '—'}",
            f"`messages`   {msg_count}",
            f"`last`       {last}",
            "",
            f"`heartbeat`  {hb_every}",
            f"`hb.target`  {hb_target}{hb_extra}",
            f"`hb.next`    {hb_next_str}",
        ]
        return "\n".join(lines)

    # ── /allowlist ───────────────────────────────────────────

    def _cmd_allowlist(self) -> str:
        tools_allow = self._config.agent.toolsAllow
        if tools_allow == "all":
            return "\n".join(
                [
                    "**Tool allowlist**: `all` (no restrictions)",
                    "",
                    "All tools are available. Set `agent.toolsAllow` in selma.json",
                    "to a list of tool names to restrict access.",
                ]
            )
        allowed = list(tools_allow) if not isinstance(tools_allow, list) else tools_allow
        lines = [f"**Tool allowlist** ({len(allowed)} tool(s))", ""]
        for name in sorted(allowed):
            lines.append(f"• `{name}`")
        disabled = [n for n in ALL_TOOL_NAMES if n not in allowed]
        if disabled:
            lines += ["", f"Disabled: {', '.join(f'`{n}`' for n in disabled)}"]
        return "\n".join(lines)

    # ── /tools ───────────────────────────────────────────────

    def _cmd_tools(self) -> str:
        allowed = resolve_tools_allow(self._config)
        active = allowed if allowed is not None else ALL_TOOL_NAMES
        descriptions = get_tool_descriptions()

        lines = [f"**Active tools** ({len(active)}/{len(ALL_TOOL_NAMES)})", ""]
        for name in active:
            desc = descriptions.get(name, "")
            lines.append(f"`{name}` — {desc}" if desc else f"`{name}`")

        if allowed is not None and len(allowed) < len(ALL_TOOL_NAMES):
            disabled = [n for n in ALL_TOOL_NAMES if n not in allowed]
            lines += ["", f"Disabled: {', '.join(f'`{n}`' for n in disabled)}"]

        return "\n".join(lines)

    # ── /skills ──────────────────────────────────────────────

    def _cmd_skills(self) -> str:
        workspace_dir = get_workspace(self._cwd)
        files = _find_skill_files(workspace_dir)
        if not files:
            return "No skills found."
        lines = [f"**Skills** ({len(files)})", ""]
        for path in files:
            fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
            name = fm.get("name", path.parent.name)
            desc = fm.get("description", "")
            lines.append(f"`{name}` — {desc}" if desc else f"`{name}`")
        return "\n\n".join(lines)

    # ── /help ────────────────────────────────────────────────

    def _cmd_help(self) -> str:
        commands = [
            ("/model [name]", "show or set the active model"),
            ("/models", "list models available in Ollama"),
            ("/think <level>", "show or set thinking level (off/low/medium/high)"),
            ("/reset /new", "reset the current session"),
            ("/compact", "compact the session context to save tokens"),
            ("/config show", "show current selma.json"),
            ("/tools", "list active tools"),
            ("/allowlist", "show tool allowlist from selma.json"),
            ("/status", "show runtime status (model, session, messages)"),
            ("/commands", "show full command catalog"),
            ("/skills", "list all available skills"),
            ("/help", "show this help"),
            ("/healthcheck", "run Selma system health check"),
        ]

        lines = ["**Commands**", ""]
        for cmd, desc in commands:
            lines.append(f"`{cmd}`")
            lines.append(f"  {desc}")
            lines.append("")

        workspace_dir = get_workspace(self._cwd)
        files = _find_skill_files(workspace_dir)
        if files:
            lines += ["**Skills**", ""]
            for path in files:
                fm = _parse_frontmatter(path.read_text(encoding="utf-8"))
                name = fm.get("name", path.parent.name)
                desc = fm.get("description", "")
                lines.append(f"`{name}`")
                lines.append(f"  {desc}" if desc else "")
                lines.append("")

        return "\n".join(lines).rstrip()

    # ── /config ──────────────────────────────────────────────

    def _cmd_config(self, args: list[str]) -> str:
        if not args or args[0] != "show":
            return "Usage: `/config show`"
        config_path = Path(".selma/selma.json")
        if not config_path.exists():
            return "Config file not found."
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return f"```json\n{json.dumps(data, indent=2)}\n```"
