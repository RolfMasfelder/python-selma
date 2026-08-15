from __future__ import annotations

from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field

# ─── TOOL DESCRIPTIONS ──────────────────────────────────────

TOOL_DESCRIPTIONS: dict[str, str] = {
    "read": "Read file contents (with optional offset/limit for large files)",
    "write": "Write or overwrite a file (creates parent directories automatically)",
    "edit": "Replace exact text in a file (surgical, unique-match edit)",
    "ls": "List directory contents",
    "grep": "Search file contents for a regex or literal pattern",
    "find": "Search for files by glob pattern",
}

# Convenience groupings matching index.ts
CODING_TOOLS = ["read", "edit", "write"]
READ_ONLY_TOOLS = ["read", "grep", "find", "ls"]
ALL_TOOLS = list(TOOL_DESCRIPTIONS.keys())


# ─── OPTIONS ────────────────────────────────────────────────


class ContextFile(BaseModel):
    path: str
    content: str


class BuildSystemPromptOptions(BaseModel):
    custom_prompt: str | None = None
    """ Replaces the default prompt entirely. """

    selected_tools: list[str] = Field(default_factory=lambda: list(CODING_TOOLS))
    """ Which tools are mentioned in the prompt. """

    tool_descriptions: dict[str, str] = Field(default_factory=dict)
    """
    Tool name → description for the tools actually in use.
    Takes precedence over TOOL_DESCRIPTIONS for known tools,
    and covers custom tools not listed in TOOL_DESCRIPTIONS.
    """

    prompt_guidelines: list[str] = Field(default_factory=list)
    """ Additional guideline bullets appended after the auto-derived ones. """

    append_system_prompt: str | None = None
    """ Text appended at the end (always, even with custom_prompt). """

    cwd: str | None = None
    """ Working directory shown in the prompt. Default: process cwd. """

    context_files: list[ContextFile] = Field(default_factory=list)
    """ Preloaded context files, e.g. AGENTS.md. """


# ─── FUNCTION ───────────────────────────────────────────────


def build_system_prompt(options: BuildSystemPromptOptions | None = None) -> str:
    """
    Builds the final system prompt string.

    Order:
      1. custom_prompt OR default prompt (tool list + auto-derived guidelines)
      2. + append_system_prompt (always appended)
      3. + context_files (e.g. AGENTS.md content)
      4. + current date and cwd
    """
    if options is None:
        options = BuildSystemPromptOptions()

    resolved_cwd = options.cwd or str(Path.cwd())
    today = date.today().isoformat()
    append_section = f"\n\n{options.append_system_prompt}" if options.append_system_prompt else ""

    # ── Custom Prompt ────────────────────────────────────────
    if options.custom_prompt:
        prompt = options.custom_prompt
        prompt += append_section
        prompt += _build_context_section(options.context_files)
        prompt += f"\nCurrent date: {today}"
        prompt += f"\nCurrent working directory: {resolved_cwd}"
        return prompt

    # ── Default Prompt ───────────────────────────────────────

    # Tools list (only include tools that are known)
    # Merge: caller-supplied descriptions take precedence over the built-in registry.
    # Tools not found in either dict are still listed — with just their name.
    merged_descriptions = {**TOOL_DESCRIPTIONS, **options.tool_descriptions}

    tools_list = (
        "\n".join(
            f"- {name}: {merged_descriptions[name]}" if name in merged_descriptions else f"- {name}"
            for name in options.selected_tools
        )
        or "(none)"
    )

    # Auto-derive guidelines from selected tools
    selected = set(options.selected_tools)
    guidelines: list[str] = []

    if "read" in selected:
        guidelines.append("Use read to examine files. Never use bash cat or sed to read files.")
    if "ls" in selected:
        guidelines.append("Use ls to explore directory structure.")
    if "read" in selected and "ls" in selected:
        guidelines.append("Use ls first to understand the directory structure, then read for file details.")
    if "grep" in selected:
        guidelines.append("Use grep to search file contents. Prefer literal=true for plain-text searches.")
    if "find" in selected:
        guidelines.append("Use find to locate files by glob pattern.")
    if "edit" in selected:
        guidelines.append("Use edit for surgical changes to existing files. The old_text must appear exactly once.")
    if "write" in selected:
        guidelines.append("Use write to create new files or fully replace existing ones.")
    if "bash" in selected:
        guidelines.append(
            "Use bash for tasks that cannot be done with the other tools "
            "(running tests, git commands, package managers, etc.)."
        )

    # Append any caller-supplied guidelines (deduplicated)
    for g in options.prompt_guidelines:
        g = g.strip()
        if g and g not in guidelines:
            guidelines.append(g)

    guidelines.append("Be concise in your responses.")
    guidelines.append("Show file paths clearly when working with files.")

    guidelines_str = "\n".join(f"- {g}" for g in guidelines)

    prompt = f"""You are a personal assistant Answer all questions truthfully and to the best of your ability.

Available tools:
{tools_list}

Guidelines:
{guidelines_str}"""

    prompt += append_section
    prompt += _build_context_section(options.context_files)
    prompt += f"\nCurrent date: {today}"
    prompt += f"\nCurrent working directory: {resolved_cwd}"

    return prompt


# ─── HELPER ─────────────────────────────────────────────────


def _build_context_section(context_files: list[ContextFile]) -> str:
    """Appends context files (e.g. AGENTS.md) to the prompt."""
    if not context_files:
        return ""

    section = "\n\n# Project Context\n\n"
    section += "Project-specific instructions and guidelines:\n\n"
    for f in context_files:
        section += f"## {f.path}\n\n{f.content}\n\n"
    return section
