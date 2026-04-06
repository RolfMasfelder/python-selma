# ============================================================
# my_mono/system_prompt.py
# ============================================================

from __future__ import annotations
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field


# ─── TOOL DESCRIPTIONS ──────────────────────────────────────

TOOL_DESCRIPTIONS: dict[str, str] = {
    "read_file":       "Read file contents",
    "list_directory":  "List directory contents",
}


# ─── OPTIONS ────────────────────────────────────────────────

class ContextFile(BaseModel):
    path: str
    content: str


class BuildSystemPromptOptions(BaseModel):
    custom_prompt: str | None = None
    """ Replaces the default prompt entirely. """

    selected_tools: list[str] = Field(
        default_factory=lambda: ["read_file", "list_directory"]
    )
    """ Which tools are mentioned in the prompt. """

    prompt_guidelines: list[str] = Field(default_factory=list)
    """ Additional guideline bullets. """

    append_system_prompt: str | None = None
    """ Text appended at the end. """

    cwd: str | None = None
    """ Working directory. Default: current directory. """

    context_files: list[ContextFile] = Field(default_factory=list)
    """ Preloaded context files e.g. AGENTS.md """


# ─── FUNCTION ───────────────────────────────────────────────

def build_system_prompt(options: BuildSystemPromptOptions | None = None) -> str:
    """
    Builds the final system prompt string.

    Order:
      1. custom_prompt OR default prompt with tool and guideline list
      2. + append_system_prompt (always appended)
      3. + context_files (e.g. AGENTS.md content)
      4. + date and cwd
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

    # Tools list
    tools_list = "\n".join(
        f"- {name}: {TOOL_DESCRIPTIONS[name]}"
        for name in options.selected_tools
        if name in TOOL_DESCRIPTIONS
    ) or "(none)"

    # Derive guidelines dynamically from available tools
    guidelines: list[str] = []

    has_read = "read_file" in options.selected_tools
    has_ls   = "list_directory" in options.selected_tools

    if has_read:
        guidelines.append(
            "Use read_file to examine files. Never use bash cat or sed to read files."
        )
    if has_ls:
        guidelines.append(
            "Use list_directory to explore the file system."
        )
    if has_read and has_ls:
        guidelines.append(
            "Use list_directory first to understand the structure, then read_file for details."
        )

    for g in options.prompt_guidelines:
        g = g.strip()
        if g and g not in guidelines:
            guidelines.append(g)

    guidelines.append("Be concise in your responses.")
    guidelines.append("Show file paths clearly when working with files.")

    guidelines_str = "\n".join(f"- {g}" for g in guidelines)

    prompt = f"""You are an expert coding assistant. \
You help users by reading files and exploring directory structures.

Available tools:
{tools_list}

Guidelines:
{guidelines_str}"""

    prompt += append_section
    prompt += _build_context_section(options.context_files)
    prompt += f"\nCurrent date: {today}"
    prompt += f"\nCurrent working directory: {resolved_cwd}"

    return prompt


# ─── HELPER FUNCTION ────────────────────────────────────────

def _build_context_section(context_files: list[ContextFile]) -> str:
    """Appends context files (e.g. AGENTS.md) to the prompt."""
    if not context_files:
        return ""

    section = "\n\n# Project Context\n\n"
    section += "Project-specific instructions and guidelines:\n\n"
    for f in context_files:
        section += f"## {f.path}\n\n{f.content}\n\n"
    return section