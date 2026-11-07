# ============================================================
# my_tools_exec.py
#
# Shell execution tool: exec (bash, sync, truncated output).
# (Python port of bash.ts)
#
# execute() is synchronous — pydantic_agent.py runs it via
# asyncio.to_thread() automatically.
#
# `subprocess` is a module-level import: tests and integrations
# may patch the my_tools.subprocess handle (re-exported by the
# my_tools façade) to control process execution deterministically.
# ============================================================

from __future__ import annotations

import os
import subprocess  # noqa: F401  (re-exported: patchable process handle)
from pathlib import Path
from typing import Any

from selma.agent import AgentTool, ToolSchema
from selma.my_tools_common import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    _truncate_head,
)


def make_exec_tool(cwd: str) -> AgentTool:
    """
    Execute a shell command via bash.

    Parameters:
      command   (required) — The shell command to run.
      timeout   (optional, default 60s) — Maximum wall-clock time in seconds.
      yieldMs   (optional)             — For long-running processes: how many
                                        milliseconds to wait before returning
                                        a partial result. When absent the process
                                        runs until completion or timeout.

    Output is truncated to DEFAULT_MAX_LINES lines or DEFAULT_MAX_BYTES bytes.
    Mirrors createBashTool() from bash.ts, Python-style.
    """
    DEFAULT_EXEC_TIMEOUT = 60  # seconds

    def execute(
        command: str,
        timeout: int | None = None,
        yieldMs: int | None = None,
        **_: Any,
    ) -> str:
        effective_timeout = timeout if timeout is not None else DEFAULT_EXEC_TIMEOUT

        env = os.environ.copy()
        env["PATH"] = env.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin"

        try:
            result = subprocess.run(
                ["/bin/bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                cwd=str(Path(cwd).resolve()),
                env=env,
            )
        except subprocess.TimeoutExpired as e:
            return (
                f"Error: command timed out after {effective_timeout}s. "
                f"Increase the timeout parameter or use a background job with 'nohup'/'&'.\n"
                f"Partial output:\n{str(e.stdout)[:200] or '(none)'}"
            )
        except Exception as e:
            return f"Error executing command: {e}"

        output = result.stdout + (result.stderr or "")

        # Truncate to limits
        output = _truncate_head(output, max_lines=DEFAULT_MAX_LINES, max_bytes=DEFAULT_MAX_BYTES)

        suffix = ""
        if result.returncode != 0:
            suffix = f"\n\n[Command exited with code {result.returncode}]"

        return output + suffix

    return AgentTool(
        name="exec",
        description=(
            "Execute a shell command via bash. Runs synchronously in the workspace directory. "
            "Use this to run scripts, install packages, compile code, manage Docker containers, etc."
        ),
        parameters=ToolSchema(
            properties={
                "command": {
                    "type": "string",
                    "description": "The shell command to run",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Maximum wall-clock time in seconds (default: 60)",
                },
                "yieldMs": {
                    "type": "integer",
                    "description": "For long-running processes: how many milliseconds to wait before returning a partial result",
                },
            },
            required=["command"],
        ),
        execute=execute,
    )
