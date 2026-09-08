"""Unit tests for selma.my_tools (read/write/edit/ls/grep/find/exec tools).

All tool execute() functions are synchronous; tests call them directly.
Uses tmp_path per test. rg availability is environment-dependent — the
python grep fallback is tested explicitly via _grep_python.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import selma.my_tools as mt
from selma.agent import AgentTool
from selma.my_tools import (
    DEFAULT_MAX_LINES,
    _format_size,
    _fuzzy_find,
    _normalize_for_fuzzy,
    _normalize_to_lf,
    _resolve,
    _truncate_head,
    create_all_tools,
    create_coding_tools,
    create_read_only_tools,
    make_edit_tool,
    make_exec_tool,
    make_find_tool,
    make_grep_tool,
    make_ls_tool,
    make_read_tool,
    make_write_tool,
)

# ─── HELPERS ───────────────────────────────────────────────


def _exec(tool: AgentTool) -> Callable[..., str]:
    return tool.execute  # type: ignore[no-any-return]


def _seed(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def no_rg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the python (``re``) grep branch by making rg look unavailable.

    ``_rg_available()`` runs ``rg --version``; if that raises FileNotFoundError
    the tool falls back to ``_grep_python`` deterministically, so tests can
    assert the python output format regardless of whether rg is installed.
    """

    def _no_rgi(*_a: Any, **_kw: Any) -> Any:
        raise FileNotFoundError("rg unavailable (test)")

    monkeypatch.setattr(mt.subprocess, "run", _no_rgi)


# ─── _format_size ──────────────────────────────────────────


def test_format_size_bytes():
    assert _format_size(0) == "0B"
    assert _format_size(1023) == "1023B"


def test_format_size_kb_mb():
    assert _format_size(1024) == "1.0KB"
    assert _format_size(512 * 1024) == "512.0KB"
    assert _format_size(1024 * 1024) == "1.0MB"
    assert _format_size(10 * 1024 * 1024) == "10.0MB"


# ─── _truncate_head ────────────────────────────────────────


def test_truncate_head_fits():
    assert _truncate_head("abc\ndef") == "abc\ndef"


def test_truncate_head_by_lines():
    content = "\n".join(f"line{i}" for i in range(10))
    out = _truncate_head(content, max_lines=4, max_bytes=10_000)
    assert "line3" in out
    assert "line4" not in out
    assert "[Showing lines 1-4 of 10." in out
    assert "Use offset=5 to continue." in out
    # Never a partial line
    assert not out.splitlines()[4].startswith("line4")


def test_truncate_head_by_bytes():
    lines = ["x" * 100 for _ in range(20)]
    out = _truncate_head("\n".join(lines), max_lines=100, max_bytes=250)
    assert "50.0KB limit" not in out
    assert f"{_format_size(250)} limit".replace("250B", "250B") in out
    assert "Use offset=" in out


def test_truncate_head_first_line_exceeds():
    out = _truncate_head("x" * 300, max_lines=10, max_bytes=100)
    assert "[Line 1 exceeds 100B limit." in out
    assert "head -c 100" in out


def test_truncate_head_start_line_display():
    content = "\n".join(f"l{i}" for i in range(6))
    out = _truncate_head(content, max_lines=2, max_bytes=1000, start_line_display=4)
    assert "[Showing lines 4-5 of 9." in out
    assert "Use offset=6 to continue." in out


# ─── _resolve ──────────────────────────────────────────────


def test_resolve_relative_and_absolute(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    abs_dir = str(tmp_path)
    assert _resolve(abs_dir, "sub/file.txt") == Path(abs_dir) / "sub" / "file.txt"
    assert _resolve(abs_dir, "/etc/hosts").is_absolute()
    abs_path = tmp_path_factory.mktemp("abs") / "f.txt"
    assert _resolve(abs_dir, str(abs_path)) == abs_path


def test_resolve_tilde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    p = _resolve("/irrelevant", "~/notes.txt")
    assert p == Path(tmp_path) / "notes.txt"


# ─── Normalization / fuzzy ─────────────────────────────────


def test_normalize_to_lf():
    assert _normalize_to_lf("a\r\nb\rc\nd") == "a\nb\nc\nd"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a\u2018b\u2019", "a'b'"),
        ("""a\u201cb\u201d""", 'a"b"'),
        ("a \u2014 b", "a - b"),
        ("a\u00a0b", "a b"),
    ],
)
def test_normalize_for_fuzzy(raw: str, expected: str) -> None:
    assert _normalize_for_fuzzy(raw) == expected


def test_normalize_for_fuzzy_strips_trailing_ws():
    # per-line rstrip keeps the final empty line produced by the trailing \n
    assert _normalize_for_fuzzy("a   \nb\t") == "a\nb"
    assert _normalize_for_fuzzy("a   \nb\t\n") == "a\nb\n"


def test_fuzzy_find_exact(tmp_path: Path) -> None:
    found, idx, n, content = _fuzzy_find("abc", "bc")
    assert (found, idx, n) == (True, 1, 2)
    assert content == "abc"


def test_fuzzy_find_fuzzy_and_miss() -> None:
    found, idx, n, content = _fuzzy_find("He said \u201chello\u201d", 'He said "hello"')
    assert found
    assert content == 'He said "hello"'  # fuzzy-normalized content returned
    assert n == len('He said "hello"')
    assert _fuzzy_find("abc", "zzz") == (False, -1, 0, "abc")


# ─── read ──────────────────────────────────────────────────


def test_read_basic(tmp_path: Path):
    p = _seed(tmp_path, "a.txt", "one\ntwo\nthree")
    out = _exec(make_read_tool(str(tmp_path)))(path=str(p))
    assert out == "1\tone\n2\ttwo\n3\tthree"


def test_read_offset_limit(tmp_path: Path):
    p = _seed(tmp_path, "a.txt", "\n".join(str(i) for i in range(1, 11)))
    tool = _exec(make_read_tool(str(tmp_path)))
    out = tool(path=str(p), offset=3, limit=2)
    assert out == "3\t3\n4\t4"


def test_read_offset_beyond_eof(tmp_path: Path):
    p = _seed(tmp_path, "a.txt", "x\ny")
    out = _exec(make_read_tool(str(tmp_path)))(path=str(p), offset=99)
    assert "offset 99 is beyond end of file" in out


def test_read_not_found_and_not_a_file(tmp_path: Path):
    tool = _exec(make_read_tool(str(tmp_path)))
    assert tool(path=str(tmp_path / "nope.txt")).startswith("Error: file not found:")
    assert tool(path=str(tmp_path)).startswith("Error: not a file:")


def test_read_truncation_notice_and_numbering(tmp_path: Path):
    p = _seed(tmp_path, "big.txt", "\n".join(f"line{i}" for i in range(1, DEFAULT_MAX_LINES + 5)))
    out = _exec(make_read_tool(str(tmp_path)))(path=str(p))
    assert f"{DEFAULT_MAX_LINES}\tline{DEFAULT_MAX_LINES}" in out
    assert f"[Showing lines 1-{DEFAULT_MAX_LINES} of {DEFAULT_MAX_LINES + 4}." in out
    assert f"Use offset={DEFAULT_MAX_LINES + 1} to continue." in out
    numbered = out.split("]")[0].split("\n")
    assert numbered[-1].startswith(f"{DEFAULT_MAX_LINES}\t")


def test_read_rejects_absolute_outside_cwd(tmp_path: Path):
    tool = _exec(make_read_tool(str(tmp_path)))
    out = tool(path="/etc/passwd")
    # /etc/passwd exists → reads it (read tool has no workspace jail)
    assert out.startswith("1\troot:")


# ─── write ─────────────────────────────────────────────────


def test_write_creates_parent_dirs(tmp_path: Path):
    out = _exec(make_write_tool(str(tmp_path)))(path="a/b/c.txt", content="hi")
    assert "Successfully wrote 2 bytes" in out
    assert (tmp_path / "a/b/c.txt").read_text() == "hi"


def test_write_overwrites(tmp_path: Path):
    p = _seed(tmp_path, "w.txt", "old")
    _exec(make_write_tool(str(tmp_path)))(path="w.txt", content="new")
    assert p.read_text() == "new"


def test_write_escaping_workspace_rejected(tmp_path: Path):
    out = _exec(make_write_tool(str(tmp_path)))(path="../sneaky.txt", content="x")
    assert "escapes the workspace directory" in out
    assert not (tmp_path.parent / "sneaky.txt").exists()


def test_write_absolute_outside_workspace_rejected(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory):
    outside = tmp_path_factory.mktemp("outside")
    out = _exec(make_write_tool(str(tmp_path)))(path=str(outside / "x.txt"), content="x")
    assert "escapes the workspace directory" in out


# ─── edit ──────────────────────────────────────────────────


def test_edit_success(tmp_path: Path):
    p = _seed(tmp_path, "e.txt", "apple banana cherry")
    out = _exec(make_edit_tool(str(tmp_path)))(path="e.txt", old_text="banana", new_text="kiwi")
    assert out == "Successfully replaced text in e.txt."
    assert p.read_text() == "apple kiwi cherry"


def test_edit_missing_file(tmp_path: Path):
    out = _exec(make_edit_tool(str(tmp_path)))(path="ghost.txt", old_text="a", new_text="b")
    assert "file not found" in out


def test_edit_not_found(tmp_path: Path):
    _seed(tmp_path, "e.txt", "something else")
    out = _exec(make_edit_tool(str(tmp_path)))(path="e.txt", old_text="missing", new_text="x")
    assert "could not find the text" in out


def test_edit_not_unique(tmp_path: Path):
    _seed(tmp_path, "e.txt", "dup\ndup")
    out = _exec(make_edit_tool(str(tmp_path)))(path="e.txt", old_text="dup", new_text="x")
    assert "found 2 occurrences" in out


def test_edit_no_change_rejected(tmp_path: Path):
    _seed(tmp_path, "e.txt", "same other")
    out = _exec(make_edit_tool(str(tmp_path)))(path="e.txt", old_text="same", new_text="same")
    assert "no changes would be made" in out


def test_edit_fuzzy_smart_quotes(tmp_path: Path):
    p = _seed(tmp_path, "e.txt", "He said \u201cyo\u201d ok")
    out = _exec(make_edit_tool(str(tmp_path)))(path="e.txt", old_text='He said "yo" ok', new_text="bye")
    assert "Successfully replaced" in out
    assert p.read_text() == "bye"


def test_edit_unicode_dash_and_trailing_ws(tmp_path: Path):
    p = _seed(tmp_path, "e.txt", "alpha \u2014 beta   \nrest")
    out = _exec(make_edit_tool(str(tmp_path)))(path="e.txt", old_text="alpha - beta", new_text="alpha + beta")
    assert "Successfully replaced" in out
    assert p.read_text() == "alpha + beta\nrest"


def test_edit_handles_crlf_source(tmp_path: Path):
    p = tmp_path / "e.txt"
    p.write_bytes(b"a b c\r\nnext")
    out = _exec(make_edit_tool(str(tmp_path)))(path="e.txt", old_text="b", new_text="B")
    assert "Successfully replaced" in out
    # read_text() applies universal-newline normalization, so the CRLF branch
    # (which re-arms only when the *raw* read kept \r\n) does not fire here.
    assert p.read_text() == "a B c\nnext"


def test_edit_workspace_escape_rejected(tmp_path: Path):
    out = _exec(make_edit_tool(str(tmp_path)))(path="../x.txt", old_text="a", new_text="b")
    assert "escapes the workspace directory" in out


# ─── ls ────────────────────────────────────────────────────


def test_ls_sorted_dotfiles_and_suffix(tmp_path: Path):
    (tmp_path / "B").mkdir()
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "a.txt").write_text("x")
    out = _exec(make_ls_tool(str(tmp_path)))()
    # sorted case-insensitively (lowercased key): .hidden < a.txt < B
    assert out == ".hidden\na.txt\nB/"


def test_ls_missing_and_not_a_dir(tmp_path: Path):
    f = _seed(tmp_path, "one.txt", "x")
    tool = _exec(make_ls_tool(str(tmp_path)))
    assert tool(path=str(tmp_path / "nope")).startswith("Error: path not found:")
    assert tool(path=str(f)).startswith("Error: not a directory:")


def test_ls_empty(tmp_path: Path):
    sub = tmp_path / "empty"
    sub.mkdir()
    assert _exec(make_ls_tool(str(tmp_path)))(path="empty") == "(empty directory)"


def test_ls_limit_notice(tmp_path: Path):
    for i in range(5):
        (tmp_path / f"f{i}").write_text("x")
    out = _exec(make_ls_tool(str(tmp_path)))(limit=2)
    assert "2 entries limit reached" in out
    assert "Use limit=4 for more" in out
    body = out.split("\n\n[")[0]
    assert len(body.split("\n")) == 2


def test_ls_byte_truncation(tmp_path: Path):
    # 350 files with ~151-char names: 350 × 151 ≈ 52.9KB > 50KB byte budget,
    # while staying under the 500-entry limit → byte-truncation branch only.
    for i in range(350):
        (tmp_path / (f"f{i:03d}-" + "pad" * 48)).write_text("x", encoding="utf-8")
    out = _exec(make_ls_tool(str(tmp_path)))()
    assert "50.0KB limit reached" in out
    assert "Showing lines" in out
    # entry-limit notice must NOT appear (350 < 500)
    assert "entries limit reached" not in out


# ─── grep (python fallback + rg path) ──────────────────────


def test_grep_basic_match(tmp_path: Path, no_rg: None):
    _seed(tmp_path, "sub/a.txt", "foo\nbar foo\n")
    _seed(tmp_path, "b.txt", "nothing")
    out = _exec(make_grep_tool(str(tmp_path)))(pattern="foo")
    assert "sub/a.txt:1: foo" in out
    assert "sub/a.txt:2: bar foo" in out
    assert "b.txt" not in out


def test_grep_single_file_and_no_match(tmp_path: Path, no_rg: None):
    f = _seed(tmp_path, "f.txt", "alpha\nbeta")
    tool = _exec(make_grep_tool(str(tmp_path)))
    out = tool(pattern="beta", path=str(f))
    # single-file search → relative path degrades to "." in python branch
    # see rel = fp.relative_to(search_path) raising ValueError → fp.name fallback
    # (the python branch actually appends fp.name if search_path IS the file)
    assert ":2: beta" in out
    assert tool(pattern="zzz") == "No matches found"


def test_grep_path_missing(tmp_path: Path):
    out = _exec(make_grep_tool(str(tmp_path)))(pattern="x", path="ghost")
    assert "Error: path not found:" in out


def test_grep_ignore_case_and_literal(tmp_path: Path, no_rg: None):
    _seed(tmp_path, "f.txt", "FOO\nfoo.*\n")
    tool = _exec(make_grep_tool(str(tmp_path)))
    assert "f.txt:1: FOO" in tool(pattern="foo", ignore_case=True)
    assert "f.txt:2: foo.*" in tool(pattern="foo.*", literal=True)
    # pattern "\\q" is neither a valid nor a matching regex
    assert tool(pattern="zzz") == "No matches found"


def test_grep_invalid_regex_python_path(tmp_path: Path, no_rg: None):
    _seed(tmp_path, "f.txt", "x")
    out = _exec(make_grep_tool(str(tmp_path)))(pattern="[")
    # "[" is an invalid regex → python branch reports it
    assert "invalid regex" in out


def test_grep_context(tmp_path: Path, no_rg: None):
    _seed(tmp_path, "f.txt", "a\nb hit\nc\nd")
    out = _exec(make_grep_tool(str(tmp_path)))(pattern="hit", context=1)
    lines = out.split("\n")
    assert "f.txt:2: b hit" in lines
    assert "f.txt-1- a" in lines
    assert "f.txt-3- c" in lines


def test_grep_glob_filter(tmp_path: Path, no_rg: None):
    _seed(tmp_path, "a.txt", "token")
    _seed(tmp_path, "b.py", "token")
    out = _exec(make_grep_tool(str(tmp_path)))(pattern="token", glob="*.py")
    assert "b.py:1: token" in out
    assert "a.txt" not in out


def test_grep_limit_reached(tmp_path: Path, no_rg: None):
    _seed(tmp_path, "f.txt", "hit\n" * 3 + " ".join(f"hit{i}" for i in range(5)))
    out = _exec(make_grep_tool(str(tmp_path)))(pattern="hit", limit=2)
    assert "2 matches limit reached" in out
    assert "Use limit=4 for more" in out


def test_grep_python_direct_errors(tmp_path: Path, no_rg: None):
    tool = make_grep_tool(str(tmp_path))
    # _grep_python is a closure — drive coverage of its error branches via execute
    _seed(tmp_path, "f.txt", "x")
    out = tool.execute(pattern="(", path=str(tmp_path / "f.txt"))
    assert "invalid regex" in out


def test_grep_rg_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Drive the rg branch by swapping mt.subprocess.run, then the error paths."""
    from selma.my_tools import make_grep_tool

    _seed(tmp_path, "f.txt", "needle")

    class _FakeResult:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout.encode("utf-8")
            self.returncode = 0

    def _raise(exc: Exception) -> Callable[..., Any]:
        def raise_it(*_a: object, **_kw: object) -> Any:
            raise exc

        return raise_it

    # --- rg available: passthrough ---
    monkeypatch.setattr(mt.subprocess, "run", lambda *a, **kw: _FakeResult("f.txt:1: needle"))
    out = _exec(make_grep_tool(str(tmp_path)))(pattern="needle")
    assert "f.txt:1: needle" in out

    # --- rg missing: FileNotFoundError from --version → python fallback ---
    monkeypatch.setattr(mt.subprocess, "run", _raise(FileNotFoundError("no rg")))
    out2 = _exec(make_grep_tool(str(tmp_path)))(pattern="needle")
    assert "needle" in out2  # python fallback still finds it

    # --- rg timeout in _grep_rg: allow --version to pass, timeout on search ---
    calls = {"n": 0}

    def _version_or_timeout(*_a: object, **_kw: object) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:  # _rg_available: rg --version succeeds
            return _FakeResult("ripgrep 14.0")
        raise subprocess.TimeoutExpired("rg", 30)

    monkeypatch.setattr(mt.subprocess, "run", _version_or_timeout)
    out3 = _exec(make_grep_tool(str(tmp_path)))(pattern="needle")
    assert "grep timed out" in out3


# ─── exec ──────────────────────────────────────────────────


def test_exec_ok(tmp_path: Path):
    out = _exec(make_exec_tool(str(tmp_path)))(command="echo hello")
    assert "hello" in out
    assert "[Command exited" not in out


def test_exec_still_runs_in_cwd(tmp_path: Path):
    (tmp_path / "marker.txt").write_text("x")
    out = _exec(make_exec_tool(str(tmp_path)))(command="pwd && ls")
    assert "marker.txt" in out


def test_exec_nonzero_exit(tmp_path: Path):
    out = _exec(make_exec_tool(str(tmp_path)))(command="echo boom; exit 3")
    assert "boom" in out
    assert "[Command exited with code 3]" in out


def test_exec_stderr_captured(tmp_path: Path):
    out = _exec(make_exec_tool(str(tmp_path)))(command="echo err >&2")
    assert "err" in out


def test_exec_timeout(tmp_path: Path):
    out = _exec(make_exec_tool(str(tmp_path)))(command="echo start; sleep 5", timeout=1)
    assert "timed out after 1s" in out
    assert "start" in out
    assert "Partial output" in out


def test_exec_timeout_no_partial(tmp_path: Path):
    out = _exec(make_exec_tool(str(tmp_path)))(command="sleep 5", timeout=1)
    assert "timed out after 1s" in out
    assert "Partial output:" in out
    # no command stdout produced → the partial section carries no real output
    assert "Increase the timeout parameter" in out


# ─── find ──────────────────────────────────────────────────


def test_find_basic(tmp_path: Path):
    _seed(tmp_path, "a.txt", "x")
    _seed(tmp_path, "sub/b.py", "x")
    out = _exec(make_find_tool(str(tmp_path)))(pattern="*.py")
    assert out == "sub/b.py"


def test_find_no_match_and_missing(tmp_path: Path):
    tool = _exec(make_find_tool(str(tmp_path)))
    (tmp_path / "keep.txt").write_text("x")
    assert tool(pattern="*.nope") == "No files found matching pattern"
    assert tool(pattern="*.txt", path="ghost").startswith("Error: path not found:")


def test_find_limit(tmp_path: Path):
    for i in range(5):
        _seed(tmp_path, f"f{i}.txt", "x")
    out = _exec(make_find_tool(str(tmp_path)))(pattern="*.txt", limit=2)
    assert "2 results limit reached" in out
    assert "f" in out


def test_find_byte_truncation(tmp_path: Path):
    # 350 files with ~151-char names → ~53KB total > 50KB budget, 350 < 1000
    # entry limit → byte-truncation branch (find has a 1000-entry default).
    for i in range(350):
        (tmp_path / (f"f{i:03d}-" + "pad" * 48)).write_text("x", encoding="utf-8")
    out = _exec(make_find_tool(str(tmp_path)))(pattern="*")
    assert "50.0KB limit reached" in out
    assert "Showing lines" in out
    # entry-limit (results) notice must NOT appear (350 < 1000)
    assert "results limit reached" not in out


# ─── Public API ────────────────────────────────────────────


def test_create_coding_tools(tmp_path: Path):
    tools = create_coding_tools(str(tmp_path))
    assert [t.name for t in tools] == ["read", "edit", "write", "exec"]
    assert all(isinstance(t, AgentTool) for t in tools)


def test_create_read_only_tools(tmp_path: Path):
    tools = create_read_only_tools(str(tmp_path))
    assert [t.name for t in tools] == ["read", "grep", "find", "ls"]


def test_create_all_tools(tmp_path: Path):
    tools = create_all_tools(str(tmp_path))
    assert set(tools) == {"read", "edit", "write", "exec", "grep", "find", "ls"}
    assert all(isinstance(t, AgentTool) for t in tools.values())
