# ============================================================
# skills.py
#
# Skills snapshot for Selma.
#
# Skills are stored as Markdown files in the workspace:
#   <workspace>/skills/<skill-name>/SKILL.md
#
# Each SKILL.md has a YAML frontmatter with at least:
#   name:        skill identifier
#   description: trigger text shown to the agent (~100 words)
#
# The snapshot is cached in the session store and rebuilt
# only when the version hash changes (content of SKILL.md files).
# ============================================================

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from selma.session_store import SkillsSnapshot

# ─── INTERNAL HELPERS ────────────────────────────────────────


def find_skill_files(workspace_dir: str) -> list[Path]:
    """Returns sorted SKILL.md paths under <workspace>/skills/*/SKILL.md."""
    skills_dir = Path(workspace_dir) / "skills"
    if not skills_dir.exists():
        return []
    return sorted(skills_dir.glob("*/SKILL.md"))


def parse_frontmatter(text: str) -> dict[str, str]:
    """
    Extracts key/value pairs from YAML frontmatter delimited by ---.
    Handles quoted and unquoted string values.
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        m = re.match(r'^([\w-]+):\s*"?(.*?)"?\s*$', line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


# ─── PUBLIC API ───────────────────────────────────────────────


def get_skills_snapshot_version(workspace_dir: str) -> str:
    """
    Returns a short SHA-256 hash of all SKILL.md file contents.
    Returns "v0" when no skills are present.
    Changes whenever a SKILL.md is added, removed, or modified.
    """
    files = find_skill_files(workspace_dir)
    if not files:
        return "v0"
    h = hashlib.sha256()
    for path in files:
        h.update(path.read_bytes())
    return h.hexdigest()[:12]


def build_skill_snapshot(workspace_dir: str, version: str) -> SkillsSnapshot:
    """
    Scans <workspace>/skills/*/SKILL.md and builds a SkillsSnapshot.

    snapshot_text is an XML block injected into the system prompt
    by _build_skills_section() in system_prompt.py:

      <available_skills>
        <skill>
          <name>...</name>
          <description>...</description>
          <location>...</location>
        </skill>
      </available_skills>
    """
    files = find_skill_files(workspace_dir)
    if not files:
        return SkillsSnapshot(version=version)

    skill_names: list[str] = []
    xml_parts: list[str] = ["<available_skills>"]

    for path in files:
        text = path.read_text(encoding="utf-8")
        fm = parse_frontmatter(text)
        name = fm.get("name", path.parent.name)
        description = fm.get("description", "")
        skill_names.append(name)
        xml_parts += [
            "  <skill>",
            f"    <name>{name}</name>",
            f"    <description>{description}</description>",
            f"    <location>{path}</location>",
            "  </skill>",
        ]

    xml_parts.append("</available_skills>")

    return SkillsSnapshot(
        version=version,
        skill_names=skill_names,
        snapshot_text="\n".join(xml_parts),
    )
