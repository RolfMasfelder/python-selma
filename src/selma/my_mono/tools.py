# ============================================================
# my_mono/tools.py
#
# Python port of packages/coding-agent/src/core/tools/
# Tools: read, write, edit, ls, grep, find
#
# All execute() functions are synchronous — pydantic_agent.py
# runs them via asyncio.to_thread() automatically.
# ============================================================

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from selma.my_mono.agent import AgentTool, ToolSchema

# ─── CONSTANTS ───────────────────────────────────────────────

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024  # 50 KB
GREP_MAX_LINE_LENGTH = 500


# ─── TRUNCATION ──────────────────────────────────────────────


def _format_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"


def _truncate_head(
    content: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    start_line_display: int = 1,
) -> str:
    """
    Keep the first N lines / M bytes (whichever limit is hit first).
    Never returns a partial line. Appends an actionable notice.
    Mirrors truncateHead() from truncate.ts.
    """
    total_bytes = len(content.encode("utf-8"))
    lines = content.split("\n")
    total_lines = len(lines)

    if total_lines <= max_lines and total_bytes <= max_bytes:
        return content

    # Check if first line alone exceeds byte limit
    if lines and len(lines[0].encode("utf-8")) > max_bytes:
        return (
            f"[Line {start_line_display} exceeds {_format_size(max_bytes)} limit. "
            f"Use bash with head -c {max_bytes} to read it.]"
        )

    kept: list[str] = []
    byte_count = 0
    truncated_by = "lines"

    for i, line in enumerate(lines):
        if i >= max_lines:
            break
        line_bytes = len(line.encode("utf-8")) + (1 if i > 0 else 0)
        if byte_count + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        kept.append(line)
        byte_count += line_bytes

    output = "\n".join(kept)
    end_line = start_line_display + len(kept) - 1
    next_offset = end_line + 1

    if truncated_by == "lines":
        notice = (
            f"\n\n[Showing lines {start_line_display}-{end_line} of "
            f"{start_line_display + total_lines - 1}. "
            f"Use offset={next_offset} to continue.]"
        )
    else:
        notice = (
            f"\n\n[Showing lines {start_line_display}-{end_line} of "
            f"{start_line_display + total_lines - 1} "
            f"({_format_size(max_bytes)} limit). "
            f"Use offset={next_offset} to continue.]"
        )

    return output + notice


# ─── PATH UTILITIES ──────────────────────────────────────────


def _resolve(cwd: str, path_str: str) -> Path:
    """Resolves a path relative to cwd. ~ is expanded. Absolute paths pass through."""
    p = Path(os.path.expanduser(path_str))
    return p if p.is_absolute() else Path(cwd) / p


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

    def execute(path: str, offset: int | None = None, limit: int | None = None, **_) -> str:
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

    def execute(path: str, content: str, **_) -> str:
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

    def execute(path: str, old_text: str, new_text: str, **_) -> str:
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

        # Restore CRLF if the original file used it
        if "\r\n" in raw:
            new_content = new_content.replace("\n", "\r\n")

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

    def execute(path: str | None = None, limit: int | None = None, **_) -> str:
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


def make_grep_tool(cwd: str) -> AgentTool:
    """
    Search file contents for a pattern.
    Uses ripgrep (rg) when available, falls back to Python re.
    Output truncated to 100 matches or 50KB.
    Mirrors createGrepTool() from grep.ts.
    """
    DEFAULT_GREP_LIMIT = 100

    def _rg_available() -> bool:
        try:
            subprocess.run(["rg", "--version"], capture_output=True, timeout=3)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _grep_rg(
        pattern: str,
        search_path: str,
        glob: str | None,
        ignore_case: bool,
        literal: bool,
        context: int,
        limit: int,
    ) -> tuple[list[str], bool]:
        args = ["rg", "--line-number", "--color=never", "--hidden", "--no-heading"]
        if ignore_case:
            args.append("--ignore-case")
        if literal:
            args.append("--fixed-strings")
        if glob:
            args.extend(["--glob", glob])
        if context > 0:
            args.extend(["-C", str(context)])
        # rg --max-count limits per-file; use --max-count with a high value
        # and slice afterwards for a global limit approximation
        args.extend([pattern, search_path])

        try:
            result = subprocess.run(args, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            return ["Error: grep timed out"], False
        except FileNotFoundError:
            return [], False

        lines = result.stdout.decode("utf-8", errors="replace").splitlines()
        if not lines:
            return [], False
        limit_reached = len(lines) > limit
        return lines[:limit], limit_reached

    def _grep_python(
        pattern: str,
        search_path: Path,
        glob: str | None,
        ignore_case: bool,
        literal: bool,
        context: int,
        limit: int,
    ) -> tuple[list[str], bool]:
        flags = re.IGNORECASE if ignore_case else 0
        try:
            regex = re.compile(re.escape(pattern) if literal else pattern, flags)
        except re.error as e:
            return [f"Error: invalid regex: {e}"], False

        if search_path.is_file():
            files = [search_path]
        elif glob:
            files = sorted(search_path.rglob(glob))
        else:
            files = sorted(p for p in search_path.rglob("*") if p.is_file())

        output: list[str] = []
        limit_reached = False

        for fp in files:
            try:
                file_lines = fp.read_text(encoding="utf-8", errors="replace").split("\n")
            except OSError:
                continue

            try:
                rel = str(fp.relative_to(search_path)).replace("\\", "/")
            except ValueError:
                rel = fp.name

            for lineno, line in enumerate(file_lines, 1):
                if len(output) >= limit:
                    limit_reached = True
                    break
                if regex.search(line):
                    display = (
                        line if len(line) <= GREP_MAX_LINE_LENGTH else line[:GREP_MAX_LINE_LENGTH] + "... [truncated]"
                    )
                    # Context lines before
                    for ci in range(context, 0, -1):
                        bi = lineno - 1 - ci
                        if bi >= 0:
                            output.append(f"{rel}-{lineno - ci}- {file_lines[bi]}")
                    output.append(f"{rel}:{lineno}: {display}")
                    # Context lines after
                    for ci in range(1, context + 1):
                        ai = lineno - 1 + ci
                        if ai < len(file_lines):
                            output.append(f"{rel}-{lineno + ci}- {file_lines[ai]}")
            if limit_reached:
                break

        return output, limit_reached

    def execute(
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        ignore_case: bool = False,
        literal: bool = False,
        context: int = 0,
        limit: int | None = None,
        **_,
    ) -> str:
        search_path = _resolve(cwd, path or ".")
        effective_limit = max(1, limit or DEFAULT_GREP_LIMIT)

        if not search_path.exists():
            return f"Error: path not found: {search_path}"

        if _rg_available():
            lines, limit_reached = _grep_rg(
                pattern,
                str(search_path),
                glob,
                ignore_case,
                literal,
                context,
                effective_limit,
            )
        else:
            lines, limit_reached = _grep_python(
                pattern,
                search_path,
                glob,
                ignore_case,
                literal,
                context,
                effective_limit,
            )

        if not lines:
            return "No matches found"

        raw_output = "\n".join(lines)
        total_bytes = len(raw_output.encode("utf-8"))
        notices: list[str] = []

        if limit_reached:
            notices.append(
                f"{effective_limit} matches limit reached. Use limit={effective_limit * 2} for more, or refine pattern"
            )
        if total_bytes > DEFAULT_MAX_BYTES:
            raw_output = _truncate_head(raw_output, max_lines=effective_limit, max_bytes=DEFAULT_MAX_BYTES)
            notices.append(f"{_format_size(DEFAULT_MAX_BYTES)} limit reached")

        output = raw_output
        if notices:
            output += "\n\n[" + ". ".join(notices) + "]"
        return output

    return AgentTool(
        name="grep",
        description=(
            f"Search file contents for a pattern. "
            f"Returns matching lines with file paths and line numbers. "
            f"Uses ripgrep (rg) when available. "
            f"Output is truncated to {DEFAULT_GREP_LIMIT} matches or "
            f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first). "
            f"Long lines are truncated to {GREP_MAX_LINE_LENGTH} chars."
        ),
        parameters=ToolSchema(
            properties={
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (regex or literal string)",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search (default: current directory)",
                },
                "glob": {
                    "type": "string",
                    "description": "Filter files by glob pattern, e.g. '*.py' or '**/*.test.py'",
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive search (default: false)",
                },
                "literal": {
                    "type": "boolean",
                    "description": "Treat pattern as literal string instead of regex (default: false)",
                },
                "context": {
                    "type": "integer",
                    "description": "Number of lines to show before and after each match (default: 0)",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Maximum number of matches to return (default: {DEFAULT_GREP_LIMIT})",
                },
            },
            required=["pattern"],
        ),
        execute=execute,
    )


def make_find_tool(cwd: str) -> AgentTool:
    """
    Search for files by glob pattern. Returns paths relative to the search directory.
    Truncated to 1000 results or 50KB.
    Mirrors createFindTool() from find.ts.
    """
    DEFAULT_FIND_LIMIT = 1000

    def execute(pattern: str, path: str | None = None, limit: int | None = None, **_) -> str:
        search_path = _resolve(cwd, path or ".")
        effective_limit = limit or DEFAULT_FIND_LIMIT

        if not search_path.exists():
            return f"Error: path not found: {search_path}"

        try:
            matches = sorted(search_path.rglob(pattern))
        except Exception as e:
            return f"Error searching: {e}"

        if not matches:
            return "No files found matching pattern"

        limit_reached = len(matches) > effective_limit
        matches = matches[:effective_limit]

        lines: list[str] = []
        for p in matches:
            try:
                rel = p.relative_to(search_path)
            except ValueError:
                rel = p
            lines.append(str(rel).replace("\\", "/"))

        raw_output = "\n".join(lines)
        total_bytes = len(raw_output.encode("utf-8"))
        notices: list[str] = []

        if limit_reached:
            notices.append(
                f"{effective_limit} results limit reached. Use limit={effective_limit * 2} for more, or refine pattern"
            )
        if total_bytes > DEFAULT_MAX_BYTES:
            raw_output = _truncate_head(raw_output, max_lines=effective_limit, max_bytes=DEFAULT_MAX_BYTES)
            notices.append(f"{_format_size(DEFAULT_MAX_BYTES)} limit reached")

        output = raw_output
        if notices:
            output += "\n\n[" + ". ".join(notices) + "]"
        return output

    return AgentTool(
        name="find",
        description=(
            f"Search for files by glob pattern. "
            f"Returns matching file paths relative to the search directory. "
            f"Output is truncated to {DEFAULT_FIND_LIMIT} results or "
            f"{DEFAULT_MAX_BYTES // 1024}KB (whichever is hit first)."
        ),
        parameters=ToolSchema(
            properties={
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match files, e.g. '*.py', '**/*.json', 'src/**/*.spec.py'",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in (default: current directory)",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Maximum number of results (default: {DEFAULT_FIND_LIMIT})",
                },
            },
            required=["pattern"],
        ),
        execute=execute,
    )


# ─── PUBLIC API ───────────────────────────────────────────────


def create_coding_tools(cwd: str) -> list[AgentTool]:
    """
    Create the default coding tools configured for a specific working directory.
    Equivalent to createCodingTools() from index.ts.
    Tools: read, edit, write
    """
    return [
        make_read_tool(cwd),
        make_edit_tool(cwd),
        make_write_tool(cwd),
    ]


def create_read_only_tools(cwd: str) -> list[AgentTool]:
    """
    Read-only tools for safe exploration (no write/edit).
    Equivalent to createReadOnlyTools() from index.ts.
    Tools: read, grep, find, ls
    """
    return [
        make_read_tool(cwd),
        make_grep_tool(cwd),
        make_find_tool(cwd),
        make_ls_tool(cwd),
    ]


def create_all_tools(cwd: str) -> dict[str, AgentTool]:
    """
    All available tools configured for a specific working directory.
    Equivalent to createAllTools() from index.ts.
    """
    return {
        "read": make_read_tool(cwd),
        "edit": make_edit_tool(cwd),
        "write": make_write_tool(cwd),
        "grep": make_grep_tool(cwd),
        "find": make_find_tool(cwd),
        "ls": make_ls_tool(cwd),
    }
