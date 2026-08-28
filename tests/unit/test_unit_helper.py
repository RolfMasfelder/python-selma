# ============================================================
# test_unit_helper.py
#
# Unit tests für selma/helper.py.
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

from pathlib import Path

from selma import helper


def test_resolve_state_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("SELMA_STATE_DIR", str(tmp_path / "my_state"))
    assert helper.resolve_state_dir() == tmp_path / "my_state"


def test_resolve_state_dir_env_override_expanduser(tmp_path, monkeypatch):
    monkeypatch.setenv("SELMA_STATE_DIR", "~/some/dir")
    # expanduser ersetzt ~ durch den Home-Ordner
    result = helper.resolve_state_dir()
    assert "~" not in str(result)
    assert result.parts[-2:] == ("some", "dir")


def test_resolve_state_dir_cwd_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("SELMA_STATE_DIR", raising=False)
    (tmp_path / ".selma").mkdir()
    assert helper.resolve_state_dir(cwd=str(tmp_path)) == tmp_path / ".selma"


def test_resolve_state_dir_home_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("SELMA_STATE_DIR", raising=False)
    empty = tmp_path / "no_selma_here"
    empty.mkdir()
    result = helper.resolve_state_dir(cwd=str(empty))
    assert result == Path.home() / ".selma"


def test_get_workspace_returns_workspace_subdir(tmp_path, monkeypatch):
    monkeypatch.setenv("SELMA_STATE_DIR", str(tmp_path / "st"))
    assert helper.get_workspace() == str(tmp_path / "st" / "workspace")


def test_now_ms_reasonable():
    before = helper.now_ms()
    after = helper.now_ms()
    assert after >= before
    # plausibles Unix-Timestamp in ms (nach 2020, vor 2100)
    assert 1_577_836_800_000 <= before <= 4_102_444_800_000


def test_now_iso_parseable():
    from datetime import datetime

    s = helper.now_iso()
    parsed = datetime.fromisoformat(s)  # wirft, wenn ungültig
    assert parsed.tzinfo is not None
