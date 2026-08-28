# ============================================================
# test_unit_skills.py
#
# Unit tests für selma/skills.py.
#
# NOTE: Die API nutzt <arg>/.selma/workspace/skills/*/SKILL.md
# (arg = State-Dir, z. B. CWD), nicht ein nacktes <arg>/skills.
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

from pathlib import Path

from selma import skills
from selma.session_store import SkillsSnapshot


def _skill_dir(root: Path) -> Path:
    d = root / ".selma" / "workspace" / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── find_skill_files ──────────────────────────────────────


def test_find_skill_files_missing_dir(tmp_path):
    assert skills.find_skill_files(str(tmp_path)) == []


def test_find_skill_files_sorted_and_only_skill_md(tmp_path):
    base = _skill_dir(tmp_path)
    for name in ("zeta", "alpha"):
        d = base / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: X\ndescription: y\n---\nbody", encoding="utf-8")
    (base / "alpha" / "notes.md").write_text("nein", encoding="utf-8")

    files = skills.find_skill_files(str(tmp_path))
    assert [f.parent.name for f in files] == ["alpha", "zeta"]
    assert all(f.name == "SKILL.md" for f in files)


# ── parse_frontmatter ─────────────────────────────────────


def test_parse_frontmatter_no_frontmatter():
    assert skills.parse_frontmatter("plainer text") == {}


def test_parse_frontmatter_basic_and_quoted():
    text = '---\nname: "My Skill"\ndescription: plain value\nother-key: spaced  \n---\nbody'
    fm = skills.parse_frontmatter(text)
    assert fm["name"] == "My Skill"
    assert fm["description"] == "plain value"
    assert fm["other-key"] == "spaced"


def test_parse_frontmatter_ignores_non_key_lines():
    fm = skills.parse_frontmatter("---\n- listitem\nname: A\n---\n")
    assert fm == {"name": "A"}


# ── get_skills_snapshot_version ────────────────────────────


def test_version_v0_without_skills(tmp_path):
    assert skills.get_skills_snapshot_version(str(tmp_path)) == "v0"


def test_version_changes_on_change_and_is_short(tmp_path):
    d = _skill_dir(tmp_path) / "s1"
    d.mkdir(parents=True)
    p = d / "SKILL.md"
    p.write_text("v1", encoding="utf-8")
    v1 = skills.get_skills_snapshot_version(str(tmp_path))
    assert isinstance(v1, str) and len(v1) > 0

    p.write_text("v2 andere konten", encoding="utf-8")
    v2 = skills.get_skills_snapshot_version(str(tmp_path))
    assert v1 != v2


# ── build_skill_snapshot ──────────────────────────────────


def test_snapshot_empty(tmp_path):
    snap = skills.build_skill_snapshot(str(tmp_path), "v0")
    assert isinstance(snap, SkillsSnapshot)
    assert snap.version == "v0"
    assert snap.skill_names == []
    assert snap.snapshot_text == ""


def _write_skill(root: Path, name: str, body: str = "body") -> Path:
    d = _skill_dir(root) / name
    d.mkdir(parents=True)
    p = d / "SKILL.md"
    p.write_text(
        f"---\nname: {name.capitalize()}\ndescription: does {name}\n---\n{body}",
        encoding="utf-8",
    )
    return p


def test_snapshot_builds_xml_and_names(tmp_path):
    paths = [_write_skill(tmp_path, "b_skill"), _write_skill(tmp_path, "a_skill")]
    snap = skills.build_skill_snapshot(str(tmp_path), "vX")

    assert snap.version == "vX"
    assert snap.skill_names == ["A_skill", "B_skill"]  # Frontmatter-Name, sortiert nach Dateiname
    xml = snap.snapshot_text
    assert xml.startswith("<available_skills>")
    assert xml.rstrip().endswith("</available_skills>")
    assert "<name>A_skill</name>" in xml
    assert "<description>does a_skill</description>" in xml
    for p in paths:
        assert f"<location>{p}</location>" in xml


def test_snapshot_fallback_to_folder_name_when_no_frontmatter(tmp_path):
    d = _skill_dir(tmp_path) / "fallback"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("nur body, keine frontmatter", encoding="utf-8")
    snap = skills.build_skill_snapshot(str(tmp_path), "vY")
    assert snap.skill_names == ["fallback"]
    assert "<name>fallback</name>" in snap.snapshot_text
    assert "<description></description>" in snap.snapshot_text
