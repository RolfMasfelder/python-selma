from __future__ import annotations

import logging
from pathlib import Path

from selma.my_system_prompt import ContextFile

logger = logging.getLogger(__name__)


class ResourceLoader:
    """
    Loads resources for the system prompt.
    Looks for CODING_TOOLS.md in the project directory: <cwd>/CODING_TOOLS.md
    """

    def __init__(self, cwd: str | Path = "."):
        self._cwd = Path(cwd)
        self._coding_tools_md = self._cwd / "CODING_TOOLS.md"

    def load_context_files(self) -> list[ContextFile]:
        """
        Loads CODING_TOOLS.md from <cwd>/CODING_TOOLS.md.
        Returns an empty list if the file does not exist.
        """
        if not self._coding_tools_md.exists():
            logger.debug("No CODING_TOOLS.md found | path=%s", self._coding_tools_md)
            return []

        content = self._coding_tools_md.read_text(encoding="utf-8")
        logger.info("Loaded CODING_TOOLS.md | path=%s", self._coding_tools_md)

        return [
            ContextFile(
                path=str(self._coding_tools_md),
                content=content,
            )
        ]
