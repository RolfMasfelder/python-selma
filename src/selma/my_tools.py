# ============================================================
# my_tools.py — Fassade (Re-Export)
#
# Python port of packages/coding-agent/src/core/tools/
# Tools: read, write, edit, ls, grep, find, exec
#
# Seit dem #15-Split (P3, REFACTOR_TODO.md) ist die Implementierung
# in drei Concern-Module ausgelagert:
#
#   my_tools_common.py  — geteilte Konstanten + Truncation/Path-Helfer
#   my_tools_fs.py      — dateibasierte Texttools: read, write, edit, ls
#   my_tools_search.py  — Suche: grep (rg + Python-`re`-Fallback), find
#   my_tools_exec.py    — Shell: exec
#
# Dieses Modul ist eine dünne Fassade: alle öffentlichen UND internen
# Namen werden weiter unter `selma.my_tools` importierbar gehalten,
# damit Verbraucher unverändert funktionieren:
#
#   tools.py               → from selma.my_tools import make_*_tool
#   agent_session.py       → from selma.my_tools import create_coding_tools
#   tests/unit/...         → import selma.my_tools as mt; mt.subprocess.run-Patches
#
# Hinweis: `subprocess` (und damit `mt.subprocess`) ist das globale
# subprocess-Modul — `monkeypatch.setattr(mt.subprocess, "run", …)`
# patcht also `subprocess.run` prozessweit und trifft grep UND exec,
# exakt wie zuvor, da beide Backends `subprocess.run` zur Laufzeit
# auflösen.
#
# Alle execute() Funktionen sind synchron — pydantic_agent.py
# führt sie via asyncio.to_thread() aus.
# ============================================================

from __future__ import annotations

import subprocess  # noqa: F401 — re-exportiert: Tests patchen mt.subprocess.run

from selma.agent import AgentTool  # noqa: F401  (Re-Export für ältere Import-Stellen)
from selma.my_tools_common import (  # noqa: F401  (Re-Export)
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    GREP_MAX_LINE_LENGTH,
    _format_size,
    _resolve,
    _truncate_head,
)
from selma.my_tools_exec import make_exec_tool  # noqa: F401
from selma.my_tools_fs import (  # noqa: F401
    _fuzzy_find,
    _normalize_for_fuzzy,
    _normalize_to_lf,
    make_edit_tool,
    make_ls_tool,
    make_read_tool,
    make_write_tool,
)
from selma.my_tools_search import (  # noqa: F401
    DEFAULT_GREP_LIMIT,
    _format_grep_output,
    _grep_rg_available,
    _grep_with_python,
    _grep_with_rg,
    make_find_tool,
    make_grep_tool,
)

__all__ = [
    "DEFAULT_MAX_LINES",
    "DEFAULT_MAX_BYTES",
    "GREP_MAX_LINE_LENGTH",
    "DEFAULT_GREP_LIMIT",
    "subprocess",
    "_format_size",
    "_truncate_head",
    "_resolve",
    "_normalize_to_lf",
    "_normalize_for_fuzzy",
    "_fuzzy_find",
    "_grep_rg_available",
    "_grep_with_rg",
    "_grep_with_python",
    "_format_grep_output",
    "make_read_tool",
    "make_write_tool",
    "make_edit_tool",
    "make_ls_tool",
    "make_grep_tool",
    "make_find_tool",
    "make_exec_tool",
    "create_coding_tools",
    "create_read_only_tools",
    "create_all_tools",
    "AgentTool",
]


# ─── PUBLIC BUILDER API (wie zuvor in my_tools.py) ───────────


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
        make_exec_tool(cwd),
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
        "exec": make_exec_tool(cwd),
        "grep": make_grep_tool(cwd),
        "find": make_find_tool(cwd),
        "ls": make_ls_tool(cwd),
    }
