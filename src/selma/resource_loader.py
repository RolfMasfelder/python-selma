from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from selma.helper import get_workspace
from selma.my_system_prompt import ContextFile

logger = logging.getLogger(__name__)

# Workspace files injected into the system prompt, in this order.
# Matches the order OpenClaw uses in its system prompt.
WORKSPACE_CONTEXT_FILES = [
    "AGENTS.md",
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "TOOLS.md",
    "MEMORY.md",
    "HEARTBEAT.md",
]


class ResourceLoader:
    """
    Loads workspace context files for the system prompt.
    Reads from <root>/.selma/workspace/ (via get_workspace, SELMA_STATE_DIR-aware).

    ``cwd`` is the project root (Agent-CWD), the same convention as the rest of
    the code base (helper.py / skills.py / agent_runtime.py).
    """

    def __init__(self, cwd: str | Path = "."):
        self._workspace = Path(get_workspace(cwd))

    def load_context_files(self) -> list[ContextFile]:
        """
        Loads workspace files (AGENTS.md, SOUL.md, IDENTITY.md, USER.md,
        TOOLS.md, MEMORY.md, HEARTBEAT.md) plus today's and yesterday's
        daily memory files. Missing files are skipped.

        BOOTSTRAP.md is always included:
          - has content  → full content (bootstrap mode active)
          - empty/absent → [MISSING] marker (bootstrap already done)

        Returns the list in definition order.
        """
        result: list[ContextFile] = []

        for filename in WORKSPACE_CONTEXT_FILES:
            path = self._workspace / filename
            if not path.exists():
                logger.debug("Workspace file not found, skipping | path=%s", path)
                continue
            content = path.read_text(encoding="utf-8")
            logger.info("Loaded workspace file | path=%s", path)
            result.append(ContextFile(path=str(path), content=content))

        result += self.load_daily_memory_files()
        result.append(self._load_bootstrap())
        return result

    def load_daily_memory_files(self) -> list[ContextFile]:
        """
        Loads today's and yesterday's daily memory files from
        <workspace>/memory/YYYY-MM-DD.md. Missing files are skipped silently.
        """
        memory_dir = self._workspace / "memory"
        result: list[ContextFile] = []

        for delta in (0, 1):
            day = date.today() - timedelta(days=delta)
            path = memory_dir / f"{day.isoformat()}.md"
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            if content.strip():
                logger.info("Loaded daily memory | path=%s", path)
                result.append(ContextFile(path=str(path), content=content))

        return result

    def _load_bootstrap(self) -> ContextFile:
        """
        Always returns a ContextFile for BOOTSTRAP.md.
        Content is the file text when non-empty, otherwise a [MISSING] marker.
        """
        path = self._workspace / "BOOTSTRAP.md"
        abs_path = str(path.resolve())

        if path.exists():
            content = path.read_text(encoding="utf-8")
            if content.strip():
                logger.info("Loaded BOOTSTRAP.md | path=%s", path)
                return ContextFile(path=abs_path, content=content)

        logger.debug("BOOTSTRAP.md absent or empty | path=%s", path)
        return ContextFile(
            path=abs_path,
            content=f"[MISSING] Expected at: {abs_path}",
        )
