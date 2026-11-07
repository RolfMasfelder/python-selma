# ============================================================
# my_tools_search.py
#
# Search tools: grep (ripgrep + Python re fallback), find (glob).
# (Python port of grep.ts / find.ts)
#
# All execute() functions are synchronous — pydantic_agent.py
# runs them via asyncio.to_thread() automatically.
#
# `subprocess` is a module-level import: tests rely on patching
# selma.my_tools.subprocess (re-exported here) to control rg
# availability.
# ============================================================

from __future__ import annotations

import re
import subprocess  # noqa: F401  (re-exported: tests patch <module>.subprocess.run)
from pathlib import Path
from typing import Any

from selma.agent import AgentTool, ToolSchema
from selma.my_tools_common import (
    DEFAULT_MAX_BYTES,
    GREP_MAX_LINE_LENGTH,
    _format_size,
    _resolve,
    _truncate_head,
)

# DEFAULT_GREP_LIMIT: default cap of match lines (100) — see make_grep_tool().
DEFAULT_GREP_LIMIT = 100


def _grep_rg_available() -> bool:
    try:
        subprocess.run(["rg", "--version"], capture_output=True, timeout=3)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _grep_with_rg(
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
    # rg --max-count limits per-file; use a high value and slice afterwards
    # for a global limit approximation
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


def _grep_with_python(
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
                display = line if len(line) <= GREP_MAX_LINE_LENGTH else line[:GREP_MAX_LINE_LENGTH] + "... [truncated]"
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


def _format_grep_output(
    lines: list[str],
    limit_reached: bool,
    effective_limit: int,
) -> str:
    """Shared post-processing for both grep backends: truncation, byte budget, notices."""
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


def make_grep_tool(cwd: str) -> AgentTool:
    """
    Search file contents for a pattern.
    Uses ripgrep (rg) when available, falls back to Python re.
    Output truncated to 100 matches or 50KB.
    Mirrors createGrepTool() from grep.ts.
    """

    def execute(
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        ignore_case: bool = False,
        literal: bool = False,
        context: int = 0,
        limit: int | None = None,
        **_: Any,
    ) -> str:
        search_path = _resolve(cwd, path or ".")
        effective_limit = max(1, limit or DEFAULT_GREP_LIMIT)

        if not search_path.exists():
            return f"Error: path not found: {search_path}"

        if _grep_rg_available():
            lines, limit_reached = _grep_with_rg(
                pattern,
                str(search_path),
                glob,
                ignore_case,
                literal,
                context,
                effective_limit,
            )
        else:
            lines, limit_reached = _grep_with_python(
                pattern,
                search_path,
                glob,
                ignore_case,
                literal,
                context,
                effective_limit,
            )
        return _format_grep_output(lines, limit_reached, effective_limit)

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

    def execute(pattern: str, path: str | None = None, limit: int | None = None, **_: Any) -> str:
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
