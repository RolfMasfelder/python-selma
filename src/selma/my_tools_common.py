# ============================================================
# my_tools_common.py
#
# Shared constants + helpers for the coding tools that live in
# the sibling concern modules (my_tools_fs / my_tools_search /
# my_tools_exec) and are re-exported by the my_tools façade.
#
# Kept dependency-free (no selma.agent import) so the concern
# modules can import from here without a cycle.
# ============================================================

from __future__ import annotations

import os
from pathlib import Path

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
