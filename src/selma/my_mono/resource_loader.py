from __future__ import annotations

import logging
from pathlib import Path

from selma.my_mono.system_prompt import ContextFile

logger = logging.getLogger(__name__)


class ResourceLoader:
    """
    Loads resources for the system prompt.
    Looks for AGENTS.md in the project directory: <cwd>/.my_mono/AGENTS.md
    """

    def __init__(self, cwd: str | Path = "."):
        self._cwd = Path(cwd)
        self._agents_md = self._cwd / ".my_mono" / "AGENTS.md"

    def load_context_files(self) -> list[ContextFile]:
        """
        Loads AGENTS.md from <cwd>/.my_mono/AGENTS.md.
        Returns an empty list if the file does not exist.
        """
        if not self._agents_md.exists():
            logger.debug("No AGENTS.md found | path=%s", self._agents_md)
            return []

        content = self._agents_md.read_text(encoding="utf-8")
        logger.info("Loaded AGENTS.md | path=%s", self._agents_md)

        return [
            ContextFile(
                path=str(self._agents_md),
                content=content,
            )
        ]
