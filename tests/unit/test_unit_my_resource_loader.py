# ============================================================
# test_unit_my_resource_loader.py
#
# Unit tests für selma/my_resource_loader.py.
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

from pathlib import Path

from selma.my_resource_loader import ResourceLoader


def _write_coding_tools(cwd: Path, content: str = "# Custom Tools\n") -> Path:
    ws = cwd / ".selma" / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    p = ws / "CODING_TOOLS.md"
    p.write_text(content, encoding="utf-8")
    return p


def test_loads_coding_tools_when_present(tmp_path):
    expected = _write_coding_tools(tmp_path, content="hallo tools")
    loader = ResourceLoader(cwd=str(tmp_path))
    files = loader.load_context_files()
    assert len(files) == 1
    f = files[0]
    assert f.path == str(expected)
    assert f.content == "hallo tools"


def test_returns_empty_list_when_missing(tmp_path):
    loader = ResourceLoader(cwd=str(tmp_path))
    assert loader.load_context_files() == []


def test_accepts_path_object_cwd(tmp_path):
    expected = _write_coding_tools(tmp_path)
    loader = ResourceLoader(cwd=tmp_path)  # Path statt str
    assert loader.load_context_files()[0].path == str(expected)


def test_relative_path_resolution(tmp_path, monkeypatch):
    _write_coding_tools(tmp_path)
    monkeypatch.chdir(tmp_path)
    loader = ResourceLoader(cwd=".")
    files = loader.load_context_files()
    assert len(files) == 1
    # cwd="." → relativer Pfad gegenüber tmp_path
    assert files[0].path == ".selma/workspace/CODING_TOOLS.md"
