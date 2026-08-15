from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path


def resolve_state_dir(cwd: str = ".") -> Path:
    """
    Resolves the .selma state directory using this priority:
      1. SELMA_STATE_DIR environment variable (if set)
      2. .selma in cwd (if it exists)
      3. .selma in the user's home directory (fallback)
    """
    override = os.environ.get("SELMA_STATE_DIR", "").strip()
    if override:
        return Path(override).expanduser()

    cwd_path = Path(cwd) / ".selma"
    if cwd_path.exists():
        return cwd_path

    return Path.home() / ".selma"


def get_workspace(cwd: str = ".") -> str:
    """Returns the workspace directory path (.selma/workspace)."""
    return str(resolve_state_dir(cwd) / "workspace")


def now_ms() -> int:
    """Current time in Unix milliseconds."""
    return int(datetime.now(UTC).timestamp() * 1000)


def now_iso() -> str:
    """Current time as ISO-8601 string."""
    return datetime.now(UTC).isoformat()
