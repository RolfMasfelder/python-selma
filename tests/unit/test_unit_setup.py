# ============================================================
# setup unit tests
#
# Deckung für selma/setup.py:
#   - SELMA_CONFIG_CONTENT (Struktur)
#   - setup(): frischer Lauf + zweiter Lauf (Alles-existiert-Pfade) + Fehlerfall
#   - handle_templates(): fehlt / leer / kopiert / überspringt
#   - handle_skills(): fehlt / keine Skills / sync + stale-Removal / no-op
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import json
import re
from pathlib import Path

from selma.setup import SELMA_CONFIG_CONTENT, handle_skills, handle_templates, setup


def _norm(out: str) -> str:
    """Rich bricht bei 80 Zeichen um — Whitespace normalisieren."""
    return re.sub(r"\s+", " ", out)


def test_config_structure():
    assert SELMA_CONFIG_CONTENT["agent"]["name"] == "Selma"
    assert SELMA_CONFIG_CONTENT["model"]["model"].startswith("ollama/")
    assert SELMA_CONFIG_CONTENT["channels"]["telegram"]["enabled"] is False
    assert SELMA_CONFIG_CONTENT["session"]["reset"]["at_hour"] == 4


def _run_setup(base: Path, capsys):
    setup(str(base))
    return capsys.readouterr().out


def test_setup_fresh_run_creates_full_tree(tmp_path, capsys):
    setup(str(tmp_path))
    out = capsys.readouterr().out

    selma_dir = tmp_path / ".selma"
    assert selma_dir.is_dir()

    # selma.json mit erwartetem Inhalt
    config = json.loads((selma_dir / "selma.json").read_text(encoding="utf-8"))
    assert config == SELMA_CONFIG_CONTENT

    workspace = selma_dir / "workspace"
    assert workspace.is_dir()

    # MEMORY.md (setup legt sie unter workspace/.selma/workspace/memory an)
    memory_index = workspace / ".selma/workspace/memory/MEMORY.md"
    assert memory_index.exists()
    assert "# Memory" in memory_index.read_text(encoding="utf-8")

    assert "Setup completed successfully" in out
    assert "Created config" in out


def test_setup_second_run_idempotent(tmp_path, capsys):
    setup(str(tmp_path))
    capsys.readouterr()

    out = _norm(_run_setup(tmp_path, capsys))
    assert "Directory already exists" in out
    assert "Config already exists" in out
    assert "Setup completed successfully" in out


def test_setup_survives_error(tmp_path, capsys):
    # workspace existiert als DATEI → mkdir schlägt fehl
    selma_dir = tmp_path / ".selma"
    selma_dir.mkdir()
    (selma_dir / "workspace").write_text("ich bin im Weg")

    setup(str(tmp_path))
    capsys.readouterr()
    out = _run_setup(tmp_path, capsys)

    assert "An error occurred during setup" in _norm(out)


def test_handle_templates_missing_source(tmp_path, capsys):
    handle_templates(tmp_path / "kein/verzeichniss", tmp_path)
    assert "not found. Skipping" in capsys.readouterr().out


def test_handle_templates_empty_dir(tmp_path, capsys):
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "unterverzug").mkdir()  # nur Unterverzeichnis → keine Dateien

    handle_templates(template_dir, tmp_path)
    assert "Template directory is empty" in capsys.readouterr().out


def test_handle_templates_copies_when_workspace_empty(tmp_path, capsys):
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "AGENTS.md").write_text("template agents", encoding="utf-8")
    (template_dir / "SOUL.md").write_text("template soul", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    handle_templates(template_dir, workspace)
    out = capsys.readouterr().out

    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == "template agents"
    assert (workspace / "SOUL.md").read_text(encoding="utf-8") == "template soul"
    assert "Copying 2 templates" in out
    assert "Copied: AGENTS.md" in out


def test_handle_templates_skips_when_present(tmp_path, capsys):
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "AGENTS.md").write_text("template", encoding="utf-8")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("mein eigener Inhalt", encoding="utf-8")

    handle_templates(template_dir, workspace)
    out = capsys.readouterr().out

    # Kein overwrite!
    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == "mein eigener Inhalt"
    assert "Skipping copy to prevent overwriting" in _norm(out)


def _make_skill(base: Path, name: str, extra_files: dict[str, str] | None = None) -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"# {name}", encoding="utf-8")
    for fname, content in (extra_files or {}).items():
        (skill_dir / fname).write_text(content, encoding="utf-8")
    return skill_dir


def test_handle_skills_missing_source(tmp_path, capsys):
    handle_skills(tmp_path / "skills", tmp_path / "dst")
    assert "Skipping" in capsys.readouterr().out


def test_handle_skills_no_skills_found(tmp_path, capsys):
    src = tmp_path / "skills"
    src.mkdir()
    (src / "kein-skill-ohne-md.txt").write_text("x", encoding="utf-8")

    handle_skills(src, tmp_path / "dst")
    assert "No skills found" in capsys.readouterr().out


def test_handle_skills_full_sync_with_stale_removal(tmp_path, capsys):
    src = tmp_path / "skills"
    dst_parent = tmp_path / "workspace"
    dst_parent.mkdir()
    dst = dst_parent / "skills"

    # Stale Skill in dst
    stale = dst / "stale-skill"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("alt", encoding="utf-8")

    # Zwei neue Skills, einer mit Zusatz-File
    _make_skill(src, "alpha", extra_files={"script.py": "print('a')"})
    _make_skill(src, "beta")

    handle_skills(src, dst)
    out = capsys.readouterr().out

    # New skills deployed
    assert (dst / "alpha/SKILL.md").read_text(encoding="utf-8") == "# alpha"
    assert (dst / "alpha/script.py").read_text(encoding="utf-8") == "print('a')"
    assert (dst / "beta/SKILL.md").read_text(encoding="utf-8") == "# beta"
    # Stale entfernt
    assert not (dst / "stale-skill").exists()
    assert "Removed stale skill: stale-skill" in out
    assert "2 skill(s) synced" in out


def test_handle_skills_up_to_date_resyncs_spec_but_keeps_existing(tmp_path, capsys):
    """SKILL.md wird IMMER (wieder-)synced, andere Dateien nur sofern neu.
    (Der 'all skipped'-Zweig existiert im Code nicht erreichbar —
    SKILL.md ist per Definition in files_to_copy, s. Code-Zeile 171.)"""
    src = tmp_path / "skills"
    dst_parent = tmp_path / "workspace"
    dst_parent.mkdir()
    dst = dst_parent / "skills"

    skill_name = "gamma"
    _make_skill(src, skill_name, extra_files={"script.py": "quelle"})
    # Ziel: SKILL.md ist alt (soll überschrieben werden), script.py existiert (soll bleiben)
    (dst / skill_name).mkdir(parents=True)
    (dst / skill_name / "SKILL.md").write_text("# ALT", encoding="utf-8")
    (dst / skill_name / "script.py").write_text("benutzerin", encoding="utf-8")

    handle_skills(src, dst)
    out = _norm(capsys.readouterr().out)

    # SKILL.md frisch aus der Quelle
    assert (dst / skill_name / "SKILL.md").read_text(encoding="utf-8") == "# gamma"
    # Benutzerdatei bleibt unangetastet
    assert (dst / skill_name / "script.py").read_text(encoding="utf-8") == "benutzerin"
    assert "skill(s) synced" in out
