# ============================================================
# system_prompt.py
#
# OpenClaw: src/agents/system-prompt.ts
#
# Builds the complete system prompt for the embedded agent
# runtime. Extends the existing system_prompt.py (which covers
# simple build options) with the full OpenClaw variant
# including all sections.
#
# Simplifications compared to OpenClaw:
#   - No sandbox block
#   - No ACP block
#   - No canvas/webchat block
#   - No messaging tool block
#   - HMAC owner display → sha256 (no HMAC secret)
#   - No provider prompt contributions
#   - promptMode only "full" and "minimal"
# ============================================================

from __future__ import annotations

import hashlib
import logging
import os
import platform
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from tools import get_tool_descriptions

logger = logging.getLogger(__name__)

# Inserted at the end of the stable prompt section.
# Anthropic providers can cache everything before it.
SYSTEM_PROMPT_CACHE_BOUNDARY = "---"

# Token for "nothing to say" replies
SILENT_REPLY_TOKEN = "__SILENT__"


# ════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ════════════════════════════════════════════════════════════

class EmbeddedContextFile(BaseModel):
    """
    A context file injected into the system prompt.
    Corresponds to EmbeddedContextFile in pi-embedded-helpers.ts.

    Examples: AGENTS.md, SOUL.md, USER.md, TOOLS.md
    """
    path: str
    content: str


class RuntimeInfo(BaseModel):
    """
    Runtime context that appears as the last section in the prompt.
    Corresponds to the runtimeInfo parameter in buildAgentSystemPrompt().
    """
    agent_id: str | None = None
    host: str | None = None
    os: str | None = None
    arch: str | None = None
    model: str | None = None
    default_model: str | None = None
    shell: str | None = None
    channel: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    repo_root: str | None = None
    canvas_root_dir: str | None = None


class ReactionGuidance(BaseModel):
    """
    Channel-specific hints for emoji reactions.
    Corresponds to the reactionGuidance parameter in buildAgentSystemPrompt().
    """
    level: Literal["minimal", "extensive"]
    channel: str                        # e.g. "Telegram", "Signal"


ThinkLevel = Literal["off", "low", "minimal", "medium", "high", "xhigh"]
ReasoningLevel = Literal["off", "on", "stream"]
OwnerIdDisplay = Literal["raw", "hash"]
PromptMode = Literal["full", "minimal", "none"]

# Bootstrap mode:
#   "none" → BOOTSTRAP.md does not exist or is empty, no prefix needed
#   "full" → BOOTSTRAP.md present with content, full workspace access
#            → clear instruction in user turn: "read BOOTSTRAP.md and follow it"
#
# Corresponds to BootstrapMode in bootstrap-mode.ts (OpenClaw).
BootstrapMode = Literal["none", "full"]


class BuildAgentSystemPromptParams(BaseModel):
    """
    All parameters for build_agent_system_prompt().

    Corresponds to the params object of buildAgentSystemPrompt()
    in system-prompt.ts.
    """
    workspace_dir: str

    # Model & reasoning
    default_think_level: ThinkLevel | None = None
    reasoning_level: ReasoningLevel = "off"

    # Prompt mode
    # "full"    → all sections (default, for the main agent)
    # "none"    → base line only
    prompt_mode: PromptMode = "full"

    # Owner identity
    owner_numbers: list[str] = Field(default_factory=list)
    owner_display: OwnerIdDisplay = "raw"
    owner_display_secret: str | None = None

    # Tools
    tool_names: list[str] = Field(default_factory=list)
    tool_summaries: dict[str, str] = Field(default_factory=dict)

    # Context files (AGENTS.md, SOUL.md, etc.)
    context_files: list[EmbeddedContextFile] = Field(default_factory=list)

    # Skills
    skills_prompt: str | None = None

    # Heartbeat
    heartbeat_prompt: str | None = None

    # Runtime context
    runtime_info: RuntimeInfo | None = None

    # Bootstrap mode (controls build_agent_user_prompt_prefix)
    # Evaluated by run_embedded_attempt — not in the system prompt,
    # but as a prefix for the first user turn.
    bootstrap_mode: BootstrapMode = "none"

    # Timezone of the user (e.g. "Europe/Berlin"), used in the time section
    user_timezone: str | None = None

    # Optional guidance text appended to the reaction section
    reaction_guidance: str | None = None

    # Optional hint for the reasoning XML tag name
    reasoning_tag_hint: str | None = None

    # Workspace notes (e.g. "commit your changes")
    workspace_notes: list[str] = Field(default_factory=list)

    # Whether to include the Silent Replies section.
    # Disabled by default — Ollama models tend to misuse __SILENT__.
    include_silent_replies: bool = False

    model_config = {"arbitrary_types_allowed": True}


# ════════════════════════════════════════════════════════════
# CONTEXT FILE SORTING
# ════════════════════════════════════════════════════════════

# Order of known context files in the prompt.
# Corresponds to CONTEXT_FILE_ORDER in system-prompt.ts.
CONTEXT_FILE_ORDER: dict[str, int] = {
    "agents.md":    10,
    "soul.md":      20,
    "identity.md":  30,
    "user.md":      40,
    "tools.md":     50,
    "bootstrap.md": 60,
    "memory.md":    70,
}

# Files placed below the cache boundary because they change frequently.
DYNAMIC_CONTEXT_FILE_BASENAMES: set[str] = {"heartbeat.md"}

# Filtered out from the heartbeat context to avoid blocking
# the Claude Code subscription mode.
_DEFAULT_HEARTBEAT_PROMPT_CONTEXT_BLOCK = (
    "Default heartbeat prompt:\n"
    "`Read HEARTBEAT.md if it exists (workspace context). "
    "Follow it strictly. Do not infer or repeat old tasks from prior chats. "
    "If nothing needs attention, reply HEARTBEAT_OK.`"
)


def _normalize_context_file_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def _get_context_file_basename(path: str) -> str:
    normalized = _normalize_context_file_path(path)
    return (normalized.split("/")[-1] or normalized).lower()


def _is_dynamic_context_file(path: str) -> bool:
    return _get_context_file_basename(path) in DYNAMIC_CONTEXT_FILE_BASENAMES


def _sanitize_context_file_content(content: str) -> str:
    """
    Removes the default heartbeat prompt block and excessive blank lines.
    Corresponds to sanitizeContextFileContentForPrompt() in system-prompt.ts.
    """
    result = content.replace(_DEFAULT_HEARTBEAT_PROMPT_CONTEXT_BLOCK, "")
    # More than 2 consecutive blank lines → 2
    import re
    return re.sub(r"\n{3,}", "\n\n", result)


def _sort_context_files(files: list[EmbeddedContextFile]) -> list[EmbeddedContextFile]:
    """
    Sorts context files by CONTEXT_FILE_ORDER.
    Corresponds to sortContextFilesForPrompt() in system-prompt.ts.
    """
    def sort_key(f: EmbeddedContextFile) -> tuple[int, str, str]:
        base = _get_context_file_basename(f.path)
        order = CONTEXT_FILE_ORDER.get(base, 2**31)
        return (order, base, _normalize_context_file_path(f.path))
    return sorted(files, key=sort_key)


# ════════════════════════════════════════════════════════════
# SECTION BUILDERS
# (each returns a list[str], empty if not applicable)
# ════════════════════════════════════════════════════════════

def _build_project_context_section(
    files: list[EmbeddedContextFile],
    heading: str,
    dynamic: bool,
) -> list[str]:
    """
    Corresponds to buildProjectContextSection() in system-prompt.ts.
    """
    if not files:
        return []

    lines: list[str] = [heading, ""]
    has_soul = any(_get_context_file_basename(f.path) == "soul.md" for f in files)

    if dynamic:
        lines += [
            "The following frequently-changing project context files are kept below "
            "the cache boundary when possible:",
            "",
        ]
    else:
        lines.append("The following project context files have been loaded:")
        if has_soul:
            lines.append(
                "If SOUL.md is present, embody its persona and tone. "
                "Avoid stiff, generic replies; follow its guidance unless "
                "higher-priority instructions override it."
            )
        lines.append("")

    for f in files:
        lines += [
            f"## {f.path}",
            "",
            _sanitize_context_file_content(f.content),
            "",
        ]
    return lines


def _build_heartbeat_section(
    is_minimal: bool,
    heartbeat_prompt: str | None,
) -> list[str]:
    """
    Corresponds to buildHeartbeatSection() in system-prompt.ts.
    """
    if is_minimal or not heartbeat_prompt:
        return []
    return [
        "## Heartbeats",
        "If the current user message is a heartbeat poll and nothing needs attention, "
        "reply exactly:",
        "HEARTBEAT_OK",
        'If something needs attention, do NOT include "HEARTBEAT_OK"; '
        "reply with the alert text instead.",
        "",
    ]


def _build_skills_section(
    skills_prompt: str | None,
    read_tool_name: str,
) -> list[str]:
    """
    Corresponds to buildSkillsSection() in system-prompt.ts.
    """
    trimmed = (skills_prompt or "").strip()
    if not trimmed:
        return []
    return [
        "## Skills (mandatory)",
        "Before replying: scan <available_skills> <description> entries.",
        f"- If exactly one skill clearly applies: read its SKILL.md at "
        f"<location> with `{read_tool_name}`, then follow it.",
        "- If multiple could apply: choose the most specific one, then read/follow it.",
        "- If none clearly apply: do not read any SKILL.md.",
        "Constraints: never read more than one skill up front; only read after selecting.",
        "- When a skill drives external API writes, assume rate limits: prefer fewer "
        "larger writes, avoid tight one-item loops, serialize bursts when possible, "
        "and respect 429/Retry-After.",
        trimmed,
        "",
    ]


def _build_user_identity_section(
    owner_line: str | None,
    is_minimal: bool,
) -> list[str]:
    """
    Corresponds to buildUserIdentitySection() in system-prompt.ts.
    """
    if not owner_line or is_minimal:
        return []
    return ["## Authorized Senders", owner_line, ""]


def _build_time_section(user_timezone: str | None) -> list[str]:
    """
    Corresponds to buildTimeSection() in system-prompt.ts.
    """
    if not user_timezone:
        return []
    return ["## Current Date & Time", f"Time zone: {user_timezone}", ""]


def _build_execution_bias_section(is_minimal: bool) -> list[str]:
    """
    Corresponds to buildExecutionBiasSection() in system-prompt.ts.
    """
    if is_minimal:
        return []
    return [
        "## Execution Bias",
        "If the user asks you to do the work, start doing it in the same turn.",
        "Use a real tool call or concrete action first when the task is actionable; "
        "do not stop at a plan or promise-to-act reply.",
        "Commentary-only turns are incomplete when tools are available and "
        "the next action is clear.",
        "If the work will take multiple steps or a while to finish, send one short "
        "progress update before or while acting.",
        "",
    ]


def _build_assistant_output_directives_section(is_minimal: bool) -> list[str]:
    """
    Corresponds to buildAssistantOutputDirectivesSection() in system-prompt.ts.
    """
    if is_minimal:
        return []
    return [
        "## Assistant Output Directives",
        "Use these when you need delivery metadata in an assistant message:",
        "- `MEDIA:<path-or-url>` on its own line requests attachment delivery.",
        "- `[[audio_as_voice]]` marks attached audio as a voice-note style delivery hint.",
        "- To request a native reply/quote on supported surfaces, include one reply tag "
        "in your reply:",
        "- Reply tags must be the very first token in the message: "
        "[[reply_to_current]] your reply.",
        "- [[reply_to_current]] replies to the triggering message.",
        "- Prefer [[reply_to_current]]. Use [[reply_to:<id>]] only when an id was "
        "explicitly provided.",
        "Whitespace inside the tag is allowed (e.g. [[ reply_to_current ]]).",
        "Supported tags are stripped before user-visible rendering; support depends "
        "on the current channel config.",
        "",
    ]


def _build_reaction_section(guidance: ReactionGuidance | None) -> list[str]:
    """
    Corresponds to the reactionGuidance block in buildAgentSystemPrompt().
    """
    if guidance is None:
        return []
    if guidance.level == "minimal":
        text = "\n".join([
            f"Reactions are enabled for {guidance.channel} in MINIMAL mode.",
            "React ONLY when truly relevant:",
            "- Acknowledge important user requests or confirmations",
            "- Express genuine sentiment (humor, appreciation) sparingly",
            "- Avoid reacting to routine messages or your own replies",
            "Guideline: at most 1 reaction per 5-10 exchanges.",
        ])
    else:
        text = "\n".join([
            f"Reactions are enabled for {guidance.channel} in EXTENSIVE mode.",
            "Feel free to react liberally:",
            "- Acknowledge messages with appropriate emojis",
            "- Express sentiment and personality through reactions",
            "- React to interesting content, humor, or notable events",
            "- Use reactions to confirm understanding or agreement",
            "Guideline: react whenever it feels natural.",
        ])
    return ["## Reactions", text, ""]


def _build_reasoning_section(reasoning_tag_hint: bool) -> list[str]:
    """
    Corresponds to the reasoningHint block in buildAgentSystemPrompt().
    Only for providers that use <think> tags (e.g. xAI/Grok).
    """
    if not reasoning_tag_hint:
        return []
    hint = " ".join([
        "ALL internal reasoning MUST be inside <think>...</think>.",
        "Do not output any analysis outside <think>.",
        "Format every reply as <think>...</think> then <final>...</final>, "
        "with no other text.",
        "Only the final user-visible reply may appear inside <final>.",
        "Only text inside <final> is shown to the user; everything else is discarded.",
        "Example:",
        "<think>Short internal reasoning.</think>",
        "<final>Hey there! What would you like to do next?</final>",
    ])
    return ["## Reasoning Format", hint, ""]


# ════════════════════════════════════════════════════════════
# OWNER IDENTITY
# ════════════════════════════════════════════════════════════

def _format_owner_display_id(owner_id: str, secret: str | None = None) -> str:
    """
    Returns a truncated hash of the owner ID.

    Corresponds to formatOwnerDisplayId() in system-prompt.ts.
    With secret: HMAC-SHA256. Without secret: SHA256.
    Always the first 12 hex characters.
    """
    if secret and secret.strip():
        import hmac
        digest = hmac.new(
            secret.strip().encode(),
            owner_id.encode(),
            hashlib.sha256,
        ).hexdigest()
    else:
        digest = hashlib.sha256(owner_id.encode()).hexdigest()
    return digest[:12]


def _build_owner_identity_line(
    owner_numbers: list[str],
    owner_display: OwnerIdDisplay,
    owner_display_secret: str | None = None,
) -> str | None:
    """
    Corresponds to buildOwnerIdentityLine() in system-prompt.ts.
    """
    normalized = [n.strip() for n in owner_numbers if n.strip()]
    if not normalized:
        return None
    if owner_display == "hash":
        display_ids = [
            _format_owner_display_id(oid, owner_display_secret)
            for oid in normalized
        ]
    else:
        display_ids = normalized
    return (
        f"Authorized senders: {', '.join(display_ids)}. "
        "These senders are allowlisted; do not assume they are the owner."
    )


# ════════════════════════════════════════════════════════════
# TOOL LIST
# ════════════════════════════════════════════════════════════

def _build_tool_lines(
    tool_names: list[str],
    extra_summaries: dict[str, str],
) -> list[str]:
    """
    Builds the tool list for the prompt.

    Tools appear in the order given by tool_names.
    Descriptions come from tools.get_tool_descriptions(); caller-supplied
    extra_summaries take precedence.
    """
    merged: dict[str, str] = {
        **get_tool_descriptions(),
        **{k.strip().lower(): v.strip() for k, v in extra_summaries.items()},
    }

    lines: list[str] = []
    for name in tool_names:
        stripped = name.strip()
        if not stripped:
            continue
        summary = merged.get(stripped.lower())
        lines.append(f"- {stripped}: {summary}" if summary else f"- {stripped}")

    return lines


# ════════════════════════════════════════════════════════════
# RUNTIME LINE
# ════════════════════════════════════════════════════════════

def build_runtime_line(
    runtime_info: RuntimeInfo | None = None,
    runtime_channel: str | None = None,
    runtime_capabilities: list[str] | None = None,
    default_think_level: ThinkLevel | None = None,
) -> str:
    """
    Builds the single-line runtime summary.

    Corresponds to buildRuntimeLine() in system-prompt.ts.

    Example:
      Runtime: host=mac-mini | os=Darwin 23.0 (arm64) | model=ollama/llama3.2 |
               channel=telegram | capabilities=inlineButtons | thinking=low
    """
    caps = runtime_capabilities or []
    normalized_caps = [c.strip() for c in caps if c.strip()]

    parts: list[str] = []

    if runtime_info:
        if runtime_info.agent_id:
            parts.append(f"agent={runtime_info.agent_id}")
        if runtime_info.host:
            parts.append(f"host={runtime_info.host}")
        if runtime_info.repo_root:
            parts.append(f"repo={runtime_info.repo_root}")
        if runtime_info.os:
            arch_suffix = f" ({runtime_info.arch})" if runtime_info.arch else ""
            parts.append(f"os={runtime_info.os}{arch_suffix}")
        elif runtime_info.arch:
            parts.append(f"arch={runtime_info.arch}")
        if runtime_info.model:
            parts.append(f"model={runtime_info.model}")
        if runtime_info.default_model:
            parts.append(f"default_model={runtime_info.default_model}")
        if runtime_info.shell:
            parts.append(f"shell={runtime_info.shell}")

    if runtime_channel:
        parts.append(f"channel={runtime_channel}")
        caps_str = ",".join(normalized_caps) if normalized_caps else "none"
        parts.append(f"capabilities={caps_str}")

    parts.append(f"thinking={default_think_level or 'off'}")

    return "Runtime: " + " | ".join(parts)


# ════════════════════════════════════════════════════════════
# MAIN FUNCTION
# ════════════════════════════════════════════════════════════

def build_agent_system_prompt(params: BuildAgentSystemPromptParams) -> str:
    """
    Builds the complete system prompt for the embedded agent.

    Corresponds to buildAgentSystemPrompt() in system-prompt.ts.

    Section order (identical to OpenClaw):
      *  Identity ("You are a personal assistant...")
      *  Tooling (tool list + hints)
      *  Tool Call Style
      *  Execution Bias
      *  Safety
      *  Skills (if present)
      *  Workspace
      *  Current Date & Time (if timezone present)
      *. Workspace Files (injected)
      *  Assistant Output Directives
      *  Stable context files (AGENTS.md, SOUL.md, etc.)
      *  Silent Replies (not in minimal)
      ── CACHE BOUNDARY ──────────────────────────────────────
      *  Dynamic context files (HEARTBEAT.md, etc.)
      *  Extra System Prompt / Group Chat Context
      *  Heartbeat (if configured)
      *  Runtime (last line)
    """
    prompt_mode = params.prompt_mode
    is_minimal = prompt_mode in ("minimal", "none")

    # "none" mode: base line only
    if prompt_mode == "none":
        return "You are Selma, a personal assistant."

    # ── Tool infrastructure ──────────────────────────────────
    tool_lines = _build_tool_lines(params.tool_names, params.tool_summaries)
    available_tools = {t.strip().lower() for t in params.tool_names if t.strip()}
    read_tool_name = next(
        (params.tool_names[i] for i, t in enumerate(params.tool_names)
         if t.strip().lower() == "read"), "read"
    )
    exec_tool_name = next(
        (params.tool_names[i] for i, t in enumerate(params.tool_names)
         if t.strip().lower() in ("exec", "bash")), "exec"
    )

    # ── Owner identity ───────────────────────────────────────
    owner_line = _build_owner_identity_line(
        params.owner_numbers,
        params.owner_display,
        params.owner_display_secret,
    )

    # ── Runtime ──────────────────────────────────────────────
    rt = params.runtime_info
    runtime_channel = (rt.channel or "").strip().lower() if rt else ""

    # ── Split context files ──────────────────────────────────
    valid_files = [
        f for f in params.context_files
        if f.path and f.path.strip()
    ]
    ordered = _sort_context_files(valid_files)
    stable_files = [f for f in ordered if not _is_dynamic_context_file(f.path)]
    dynamic_files = [f for f in ordered if _is_dynamic_context_file(f.path)]

    # ── Assemble prompt ──────────────────────────────────────
    lines: list[str] = [
        "You are Selma, a personal assistant.",
        "",

        # ── Tooling ──────────────────────────────────────
        "## Tooling",
        "Tool names are case-sensitive. Call tools exactly as listed.",
    ]

    if tool_lines:
        lines += tool_lines
    lines += [
        "TOOLS.md does not control tool availability; "
        "it is user guidance for how to use external tools.",
        f"For long waits, avoid rapid poll loops: use `{exec_tool_name}` with "
        f"enough yieldMs or process(action=poll, timeout=<ms>).",
        "If a task is more complex or takes longer, spawn a sub-agent. "
        "Completion is push-based: it will auto-announce when done.",
        "",

        # ── Tool Call Style ───────────────────────────────
        "## Tool Call Style",
        "Default: do not narrate routine, low-risk tool calls (just call the tool).",
        "Narrate only when it helps: multi-step work, complex/challenging problems, "
        "sensitive actions (e.g., deletions), or when the user explicitly asks.",
        "Keep narration brief and value-dense; avoid repeating obvious steps.",
        "Use plain human language for narration unless in a technical context.",
        "When a first-class tool exists for an action, use the tool directly "
        "instead of asking the user to run equivalent CLI or slash commands.",
        "When exec returns approval-pending, include the concrete /approve command "
        "from tool output as plain chat text for the user.",
        "Never execute /approve through exec or any other shell/tool path; "
        "/approve is a user-facing approval command, not a shell command.",
        "",
    ]

    # ── Execution Bias ────────────────────────────────────
    lines += _build_execution_bias_section(is_minimal)

    # ── Safety ────────────────────────────────────────────
    lines += [
        "## Safety",
        "You have no independent goals: do not pursue self-preservation, "
        "replication, resource acquisition, or power-seeking; avoid long-term "
        "plans beyond the user's request.",
        "Prioritize safety and human oversight over completion; if instructions "
        "conflict, pause and ask; comply with stop/pause/audit requests and never "
        "bypass safeguards.",
        "Do not manipulate or persuade anyone to expand access or disable safeguards. "
        "Do not copy yourself or change system prompts, safety rules, or tool policies "
        "unless explicitly requested.",
        "",
    ]

    # ── Skills ────────────────────────────────────────────
    lines += _build_skills_section(params.skills_prompt, read_tool_name)

    # ── Workspace ─────────────────────────────────────────
    lines += [
        "## Workspace",
        f"Your working directory is: {params.workspace_dir}",
        "Treat this directory as the single global workspace for file operations "
        "unless explicitly instructed otherwise.",
        "IMPORTANT: Use only simple relative paths for file operations (e.g. `memory/today.md`, "
        "`HEARTBEAT.md`). Do NOT include the workspace path itself in file paths — "
        f"never start a path with `{Path(params.workspace_dir).name}/` or "
        f"`{Path(params.workspace_dir).parent.name}/`. "
        "Writing outside the workspace directory is not permitted.",
        *[note.strip() for note in params.workspace_notes if note.strip()],
        "",
    ]

    # ── Date & Time ──────────────────────────────────────
    lines += _build_time_section(params.user_timezone)

    # ── Workspace Files ──────────────────────────────────
    lines += [
        "## Workspace Files (injected)",
        "These user-editable files are loaded by Selma and included below "
        "in Project Context.",
        "",
    ]

    # ── Output Directives ────────────────────────────────
    lines += _build_assistant_output_directives_section(is_minimal)

    # ── Reactions ────────────────────────────────────────
    lines += _build_reaction_section(params.reaction_guidance)

    # ── Reasoning Format ─────────────────────────────────
    lines += _build_reasoning_section(params.reasoning_tag_hint)

    # ── Stable context files ─────────────────────────────
    lines += _build_project_context_section(
        stable_files, "# Project Context", dynamic=False
    )

    # ── Silent Replies ───────────────────────────────────
    if not is_minimal and params.include_silent_replies:
        lines += [
            "## Silent Replies",
            f"When you have nothing to say, respond with ONLY: {SILENT_REPLY_TOKEN}",
            "",
            "⚠️ Rules:",
            "- It must be your ENTIRE message — nothing else",
            f'- Never append it to an actual response '
            f'(never include "{SILENT_REPLY_TOKEN}" in real replies)',
            "- Never wrap it in markdown or code blocks",
            "",
            f'❌ Wrong: "Here\'s help... {SILENT_REPLY_TOKEN}"',
            f'❌ Wrong: "\\"{SILENT_REPLY_TOKEN}\\""',
            f"✅ Right: {SILENT_REPLY_TOKEN}",
            "",
        ]

    # ── CACHE BOUNDARY ───────────────────────────────────────
    lines.append(SYSTEM_PROMPT_CACHE_BOUNDARY)

    # ── Dynamic context files ────────────────────────────
    dynamic_heading = (
        "# Dynamic Project Context" if stable_files else "# Project Context"
    )
    lines += _build_project_context_section(
        dynamic_files, dynamic_heading, dynamic=True
    )

    # ── Heartbeat ────────────────────────────────────────
    lines += _build_heartbeat_section(is_minimal, params.heartbeat_prompt)

    # ── Runtime (last line) ──────────────────────────────
    reasoning_level = params.reasoning_level or "off"
    runtime_line = build_runtime_line(
        runtime_info=params.runtime_info,
        runtime_channel=runtime_channel or None,
        runtime_capabilities=params.runtime_info.capabilities if params.runtime_info else [],
        default_think_level=params.default_think_level,
    )
    lines += [
        "## Runtime",
        runtime_line,
        f"Reasoning: {reasoning_level} (hidden unless on/stream). "
        "Toggle /reasoning; /status shows Reasoning when enabled.",
    ]

    # Strip empty lines and join
    return "\n".join(line for line in lines if line is not None)


# ════════════════════════════════════════════════════════════
# USER PROMPT PREFIX (Bootstrap)
# ════════════════════════════════════════════════════════════

def build_agent_user_prompt_prefix(
    bootstrap_mode: BootstrapMode = "none",
) -> str | None:
    """
    Builds an optional prefix for the first user turn.

    IMPORTANT: This text is NOT inserted into the system prompt but
    prepended to the first user turn. This is intentional: the LLM
    treats content in the user turn as an immediate instruction and
    acts on it — a system prompt hint would only be context.

      "none" → BOOTSTRAP.md does not exist or is empty. No prefix, normal run.
      "full" → BOOTSTRAP.md present. Clear instruction to read and follow it.

    Corresponds to buildAgentUserPromptPrefix() in system-prompt.ts.
    """
    if bootstrap_mode == "none":
        return None

    return "\n".join([
        "[Bootstrap pending]",
        "Please read BOOTSTRAP.md from the workspace and follow it "
        "before replying normally.",
        "Your first user-visible reply for a bootstrap-pending workspace "
        "must follow BOOTSTRAP.md, not a generic greeting.",
    ])