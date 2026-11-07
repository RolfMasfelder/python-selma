# ============================================================
# my_tools_fs.py
#
# File-system text tools: read, write, edit, ls.
# (Python port of packages/coding-agent/src/core/tools/*.{ts})
#
# All execute() functions are synchronous — pydantic_agent.py
# runs them via asyncio.to_thread() automatically.
#
# Shared constants/helpers live in my_tools_common.
# ============================================================

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from selma.agent import AgentTool, ToolSchema
from selma.my_tools_common import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    _format_size,
    _resolve,
    _truncate_head,
)

# ─── EDIT HELPERS (port of edit-diff.ts) ─────────────────────


def _normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_for_fuzzy(text: str) -> str:
    """
    Progressive normalization for fuzzy matching:
      - Strip trailing whitespace per line
      - Normalize smart quotes → ASCII
      - Normalize Unicode dashes → hyphen
      - Normalize special Unicode spaces → regular space
    Mirrors normalizeForFuzzyMatch() from edit-diff.ts.
    """
    lines = [line.rstrip() for line in text.split("\n")]
    result = "\n".join(lines)
    # Smart single quotes → '
    result = re.sub(r"[\u2018\u2019\u201A\u201B]", "'", result)
    # Smart double quotes → "
    result = re.sub(r"[\u201C\u201D\u201E\u201F]", '"', result)
    # Various Unicode dashes → -
    result = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", "-", result)
    # Special Unicode spaces → regular space
    result = re.sub(r"[\u00A0\u2002-\u200A\u202F\u205F\u3000]", " ", result)
    return result


def _fuzzy_find(content: str, old_text: str) -> tuple[bool, int, int, str]:
    """
    Returns (found, index, match_length, content_for_replacement).
    Tries exact match first, then fuzzy (normalized) match.
    Mirrors fuzzyFindText() from edit-diff.ts.
    """
    # 1. Exact match
    idx = content.find(old_text)
    if idx != -1:
        return True, idx, len(old_text), content

    # 2. Fuzzy match — work entirely in normalized space
    fuzzy_content = _normalize_for_fuzzy(content)
    fuzzy_old = _normalize_for_fuzzy(old_text)
    fuzzy_idx = fuzzy_content.find(fuzzy_old)

    if fuzzy_idx == -1:
        return False, -1, 0, content

    return True, fuzzy_idx, len(fuzzy_old), fuzzy_content


# ─── TOOL FACTORIES ──────────────────────────────────────────


def make_read_tool(cwd: str) -> AgentTool:
    """
    Read file contents with optional line range.
    offset: 1-based start line. limit: max lines to read.
    Output truncated to 2000 lines or 50KB (head truncation).
    Mirrors createReadTool() from read.ts.
    """

    def execute(path: str, offset: int | None = None, limit: int | None = None, **_: Any) -> str:
        resolved = _resolve(cwd, path)

        if not resolved.exists():
            return f"Error: file not found: {resolved}"
        if not resolved.is_file():
            return f"Error: not a file: {resolved}"

        try:
            text = resolved.read_bytes().decode("utf-8", errors="replace")
        except OSError as e:
            return f"Error reading file: {e}"

        all_lines = text.split("\n")
        total_lines = len(all_lines)

        # Apply offset (1-based → 0-based)
        start = (offset - 1) if offset else 0
        if start >= total_lines:
            return f"Error: offset {offset} is beyond end of file ({total_lines} lines total)"

        start_display = start + 1  # 1-based for notices and line numbers

        # Apply user limit
        selected_lines = all_lines[start : start + limit] if limit else all_lines[start:]
        selected = "\n".join(selected_lines)

        # Truncate
        selected = _truncate_head(selected, start_line_display=start_display)

        # Add line numbers (1-based), stop before any notice lines
        # The notice (if any) starts with "\n\n["
        notice_sep = "\n\n["
        if notice_sep in selected:
            body, notice = selected.split(notice_sep, 1)
            notice = notice_sep[2:] + notice  # restore "\n["
        else:
            body, notice = selected, ""

        numbered = "\n".join(f"{start_display + i}\t{line}" for i, line in enumerate(body.split("\n")))
        return numbered + notice

    return AgentTool(
        name="read",
        description=(
            f"Read the contents of a file. "
            f"Output is truncated to {DEFAULT_MAX_LINES} lines or "
            f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). "
            f"Use offset/limit for large files. offset is 1-based. "
            f"When you need the full file, continue with offset until complete."
        ),
        parameters=ToolSchema(
            properties={
                "path": {
                    "type": "string",
                    "description": "Path to the file to read (relative or absolute)",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (1-indexed)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read",
                },
            },
            required=["path"],
        ),
        execute=execute,
    )


def make_write_tool(cwd: str) -> AgentTool:
    """
    Write (or overwrite) a file. Parent directories are created automatically.
    Mirrors createWriteTool() from write.ts.
    """
    workspace = Path(cwd).resolve()

    def execute(path: str, content: str, **_: Any) -> str:
        resolved = _resolve(cwd, path)
        try:
            resolved.resolve().relative_to(workspace)
        except ValueError:
            return f"Error: path '{path}' escapes the workspace directory. Use paths relative to '{cwd}' only."
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content.encode('utf-8'))} bytes to {path}"
        except OSError as e:
            return f"Error writing file: {e}"

    return AgentTool(
        name="write",
        description=(
            "Write content to a file. Creates the file if it doesn't exist, "
            "overwrites if it does. Automatically creates parent directories."
        ),
        parameters=ToolSchema(
            properties={
                "path": {
                    "type": "string",
                    "description": "Path to the file to write (relative or absolute)",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file",
                },
            },
            required=["path", "content"],
        ),
        execute=execute,
    )


def make_edit_tool(cwd: str) -> AgentTool:
    """
    Replace a unique occurrence of old_text with new_text.
    Tries exact match first, then fuzzy match (trailing whitespace + Unicode normalization).
    Fails if old_text is not found or not unique.
    Mirrors createEditTool() from edit.ts + fuzzyFindText() from edit-diff.ts.
    """

    workspace = Path(cwd).resolve()

    def execute(path: str, old_text: str, new_text: str, **_: Any) -> str:
        resolved = _resolve(cwd, path)
        try:
            resolved.resolve().relative_to(workspace)
        except ValueError:
            return f"Error: path '{path}' escapes the workspace directory. Use paths relative to '{cwd}' only."

        if not resolved.exists():
            return f"Error: file not found: {path}"

        try:
            raw = resolved.read_text(encoding="utf-8")
        except OSError as e:
            return f"Error reading file: {e}"

        # Normalize line endings before matching
        content = _normalize_to_lf(raw)
        norm_old = _normalize_to_lf(old_text)
        norm_new = _normalize_to_lf(new_text)

        found, idx, match_len, content_for_replacement = _fuzzy_find(content, norm_old)

        if not found:
            return (
                f"Error: could not find the text in {path}. "
                f"The old_text must match exactly including all whitespace and newlines."
            )

        # Verify uniqueness using fuzzy-normalized content
        fuzzy_content = _normalize_for_fuzzy(content_for_replacement)
        fuzzy_old = _normalize_for_fuzzy(norm_old)
        occurrences = fuzzy_content.count(fuzzy_old)

        if occurrences > 1:
            return (
                f"Error: found {occurrences} occurrences of the text in {path}. "
                f"The text must be unique. Provide more context to make it unique."
            )

        new_content = content_for_replacement[:idx] + norm_new + content_for_replacement[idx + match_len :]

        if new_content == content_for_replacement:
            return f"Error: no changes would be made to {path}. The replacement produces identical content."

        try:
            resolved.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return f"Error writing file: {e}"

        return f"Successfully replaced text in {path}."

    return AgentTool(
        name="edit",
        description=(
            "Edit a file by replacing exact text. "
            "The old_text must match exactly (including whitespace). "
            "Minor Unicode/whitespace differences are handled via fuzzy matching. "
            "The text to replace must appear exactly once in the file. "
            "Use this for precise, surgical edits."
        ),
        parameters=ToolSchema(
            properties={
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit (relative or absolute)",
                },
                "old_text": {
                    "type": "string",
                    "description": "Exact text to find and replace (must be unique in the file)",
                },
                "new_text": {
                    "type": "string",
                    "description": "New text to replace the old text with",
                },
            },
            required=["path", "old_text", "new_text"],
        ),
        execute=execute,
    )


def make_ls_tool(cwd: str) -> AgentTool:
    """
    List directory contents, sorted case-insensitively.
    Directories have a trailing '/'. Dotfiles are included.
    Truncated to 500 entries or 50KB.
    Mirrors createLsTool() from ls.ts.
    """
    DEFAULT_LS_LIMIT = 500

    def execute(path: str | None = None, limit: int | None = None, **_: Any) -> str:
        dir_path = _resolve(cwd, path or ".")
        effective_limit = limit or DEFAULT_LS_LIMIT

        if not dir_path.exists():
            return f"Error: path not found: {dir_path}"
        if not dir_path.is_dir():
            return f"Error: not a directory: {dir_path}"

        try:
            entries = sorted(dir_path.iterdir(), key=lambda p: p.name.lower())
        except OSError as e:
            return f"Error reading directory: {e}"

        results: list[str] = []
        limit_reached = False

        for entry in entries:
            if len(results) >= effective_limit:
                limit_reached = True
                break
            results.append(entry.name + ("/" if entry.is_dir() else ""))

        if not results:
            return "(empty directory)"

        raw_output = "\n".join(results)
        notices: list[str] = []

        if limit_reached:
            notices.append(f"{effective_limit} entries limit reached. Use limit={effective_limit * 2} for more")

        total_bytes = len(raw_output.encode("utf-8"))
        if total_bytes > DEFAULT_MAX_BYTES:
            raw_output = _truncate_head(raw_output, max_lines=effective_limit, max_bytes=DEFAULT_MAX_BYTES)
            notices.append(f"{_format_size(DEFAULT_MAX_BYTES)} limit reached")

        output = raw_output
        if notices:
            output += "\n\n[" + ". ".join(notices) + "]"
        return output

    return AgentTool(
        name="ls",
        description=(
            f"List directory contents. Returns entries sorted alphabetically, "
            f"with '/' suffix for directories. Includes dotfiles. "
            f"Output is truncated to {DEFAULT_LS_LIMIT} entries or "
            f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first)."
        ),
        parameters=ToolSchema(
            properties={
                "path": {
                    "type": "string",
                    "description": "Directory to list (default: current directory)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of entries to return (default: 500)",
                },
            },
            required=[],
        ),
        execute=execute,
    )
