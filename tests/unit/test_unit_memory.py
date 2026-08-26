# ============================================================
# test_unit_memory.py
#
# Tests for Phase 1 and Phase 2 of the memory system.
#
# Unit tests   — no LLM, run immediately
# Integration  — require a running Ollama instance (end of file)
#
# Run: uv run test_unit_memory.py
# ============================================================

from selma.tracing import setup

setup()

import asyncio
import tempfile
import traceback
from datetime import date, timedelta
from pathlib import Path

from selma.tracing import tracer

# ════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════


def _make_workspace(tmp: str) -> Path:
    """Creates the minimal .selma/workspace/ structure in tmp."""
    ws = Path(tmp) / ".selma" / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "memory").mkdir(exist_ok=True)
    return ws


def _run_unit(name: str, fn) -> bool:
    try:
        fn()
        print(f"  PASS  {name}")
        return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    except Exception as e:
        print(f"  ERROR {name}: {e}")
        traceback.print_exc()
        return False


async def _run_integration(name: str, coro) -> bool:
    try:
        await coro
        print(f"  PASS  {name}")
        return True
    except AssertionError as e:
        print(f"  FAIL  {name}: {e}")
        return False
    except Exception as e:
        print(f"  ERROR {name}: {e}")
        traceback.print_exc()
        return False


# ════════════════════════════════════════════════════════════
# UNIT TESTS — ResourceLoader
# ════════════════════════════════════════════════════════════


def test_memory_md_is_loaded():
    """MEMORY.md appears in context files when present."""
    from selma.resource_loader import ResourceLoader

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        (ws / "MEMORY.md").write_text("- Gerhard likes Python\n", encoding="utf-8")

        files = ResourceLoader(cwd=tmp).load_context_files()
        names = [Path(f.path).name for f in files]

        assert "MEMORY.md" in names, f"MEMORY.md missing in: {names}"
        content = next(f.content for f in files if Path(f.path).name == "MEMORY.md")
        assert "Gerhard likes Python" in content


def test_missing_memory_md_skipped():
    """Missing MEMORY.md is silently skipped."""
    from selma.resource_loader import ResourceLoader

    with tempfile.TemporaryDirectory() as tmp:
        _make_workspace(tmp)

        files = ResourceLoader(cwd=tmp).load_context_files()
        names = [Path(f.path).name for f in files]
        assert "MEMORY.md" not in names


def test_daily_memory_today_loaded():
    """Today's daily memory file is loaded."""
    from selma.resource_loader import ResourceLoader

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        today = date.today().isoformat()
        (ws / "memory" / f"{today}.md").write_text("- Today: first conversation\n", encoding="utf-8")

        files = ResourceLoader(cwd=tmp).load_daily_memory_files()

        assert len(files) == 1, f"Expected 1 file, got {len(files)}"
        assert today in files[0].path
        assert "first conversation" in files[0].content


def test_daily_memory_yesterday_loaded():
    """Yesterday's daily memory file is loaded."""
    from selma.resource_loader import ResourceLoader

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        (ws / "memory" / f"{yesterday}.md").write_text("- Yesterday: important decision\n", encoding="utf-8")

        files = ResourceLoader(cwd=tmp).load_daily_memory_files()

        assert len(files) == 1
        assert yesterday in files[0].path
        assert "important decision" in files[0].content


def test_daily_memory_both_days_loaded():
    """Today's and yesterday's files are both loaded."""
    from selma.resource_loader import ResourceLoader

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        (ws / "memory" / f"{today}.md").write_text("Today\n", encoding="utf-8")
        (ws / "memory" / f"{yesterday}.md").write_text("Yesterday\n", encoding="utf-8")

        files = ResourceLoader(cwd=tmp).load_daily_memory_files()

        assert len(files) == 2, f"Expected 2 files, got {len(files)}"


def test_empty_daily_memory_skipped():
    """Empty daily memory files are not loaded."""
    from selma.resource_loader import ResourceLoader

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        today = date.today().isoformat()
        (ws / "memory" / f"{today}.md").write_text("   \n", encoding="utf-8")

        files = ResourceLoader(cwd=tmp).load_daily_memory_files()
        assert len(files) == 0, "Empty file should be skipped"


def test_daily_memory_in_context_files():
    """load_context_files() also includes daily memory files."""
    from selma.resource_loader import ResourceLoader

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        today = date.today().isoformat()
        (ws / "memory" / f"{today}.md").write_text("- Note today\n", encoding="utf-8")

        files = ResourceLoader(cwd=tmp).load_context_files()
        names = [Path(f.path).name for f in files]
        assert f"{today}.md" in names, f"Daily file missing in: {names}"


# ════════════════════════════════════════════════════════════
# UNIT TESTS — memory_get Tool
# ════════════════════════════════════════════════════════════


def test_memory_get_reads_full_file():
    """memory_get reads a file completely."""
    from selma.tools import make_memory_get_tool

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        (ws / "MEMORY.md").write_text("Line1\nLine2\nLine3\n", encoding="utf-8")

        # cwd = workspace dir (as runtime.py passes it)
        tool = make_memory_get_tool(str(ws))
        result = tool.execute(path="MEMORY.md")

        assert "Line1" in result
        assert "Line2" in result
        assert "Line3" in result


def test_memory_get_line_range():
    """memory_get returns the correct line range."""
    from selma.tools import make_memory_get_tool

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        (ws / "MEMORY.md").write_text("Line1\nLine2\nLine3\nLine4\n", encoding="utf-8")

        tool = make_memory_get_tool(str(ws))
        result = tool.execute(path="MEMORY.md", from_line=2, lines=2)

        assert "Line2" in result
        assert "Line3" in result
        assert "Line1" not in result
        assert "Line4" not in result


def test_memory_get_daily_file():
    """memory_get also reads daily memory files."""
    from selma.tools import make_memory_get_tool

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        today = date.today().isoformat()
        (ws / "memory" / f"{today}.md").write_text("- Note of the day\n", encoding="utf-8")

        tool = make_memory_get_tool(str(ws))
        result = tool.execute(path=f"memory/{today}.md")

        assert "Note of the day" in result


def test_memory_get_file_not_found():
    """Non-existent file returns a clear error message."""
    from selma.tools import make_memory_get_tool

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        tool = make_memory_get_tool(str(ws))
        result = tool.execute(path="not_found.md")

        assert "not found" in result.lower() or "error" in result.lower()


def test_memory_get_blocks_traversal():
    """Directory traversal attempts are blocked."""
    from selma.tools import make_memory_get_tool

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        tool = make_memory_get_tool(str(ws))

        for evil_path in ["../../etc/passwd", "../selma.json", "/etc/hosts"]:
            result = tool.execute(path=evil_path)
            assert "Error" in result, f"Traversal attack '{evil_path}' was not blocked"


# ════════════════════════════════════════════════════════════
# UNIT TESTS — MemoryIndex (Phase 2)
# ════════════════════════════════════════════════════════════


def test_index_sync_indexes_memory_md():
    """sync() indexes MEMORY.md and returns 1."""
    from selma.memory_index import MemoryIndex

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        (ws / "MEMORY.md").write_text("- Favourite colour: Blue\n", encoding="utf-8")

        idx = MemoryIndex(workspace_dir=str(ws))
        n = idx.sync()

        assert n == 1, f"Expected 1 indexed file, got {n}"


def test_index_sync_indexes_daily_files():
    """sync() indexes MEMORY.md + daily files."""
    from selma.memory_index import MemoryIndex

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        (ws / "MEMORY.md").write_text("Main notes\n", encoding="utf-8")
        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        (ws / "memory" / f"{today}.md").write_text("Today\n", encoding="utf-8")
        (ws / "memory" / f"{yesterday}.md").write_text("Yesterday\n", encoding="utf-8")

        idx = MemoryIndex(workspace_dir=str(ws))
        n = idx.sync()

        assert n == 3, f"Expected 3 indexed files, got {n}"


def test_index_no_reindex_when_unchanged():
    """Second sync() without file changes returns 0."""
    from selma.memory_index import MemoryIndex

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        (ws / "MEMORY.md").write_text("- Unchanged content\n", encoding="utf-8")

        idx = MemoryIndex(workspace_dir=str(ws))
        idx.sync()  # first sync
        n = idx.sync()  # second sync — no changes

        assert n == 0, f"Expected 0 re-indexed files, got {n}"


def test_index_reindex_on_file_change():
    """Changed file is re-indexed on the next sync()."""
    from selma.memory_index import MemoryIndex

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        mem = ws / "MEMORY.md"
        mem.write_text("- Version 1\n", encoding="utf-8")

        idx = MemoryIndex(workspace_dir=str(ws))
        idx.sync()

        mem.write_text("- Version 2\n", encoding="utf-8")
        n = idx.sync()

        assert n == 1, f"Expected 1 re-indexed file, got {n}"

        results = idx.search("Version 2")
        assert len(results) == 1
        assert "Version 2" in results[0].content


def test_index_removes_deleted_file():
    """Deleted file is removed from the index."""
    from selma.memory_index import MemoryIndex

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        today = date.today().isoformat()
        daily = ws / "memory" / f"{today}.md"
        daily.write_text("- Temporary note\n", encoding="utf-8")

        idx = MemoryIndex(workspace_dir=str(ws))
        idx.sync()

        daily.unlink()
        idx.sync()  # sync after deletion

        results = idx.search("Temporary note")
        assert len(results) == 0, "Deleted file should no longer be found"


def test_index_search_returns_matching_result():
    """search() finds content from MEMORY.md."""
    from selma.memory_index import MemoryIndex

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        (ws / "MEMORY.md").write_text("## Preferences\n- Favourite language: Python\n", encoding="utf-8")

        idx = MemoryIndex(workspace_dir=str(ws))
        idx.sync()
        results = idx.search("Python")

        assert len(results) == 1
        assert "Python" in results[0].content
        assert results[0].path == "MEMORY.md"


def test_index_search_finds_daily_file():
    """search() finds content from a daily file."""
    from selma.memory_index import MemoryIndex

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        today = date.today().isoformat()
        (ws / "memory" / f"{today}.md").write_text("- Selma Phase 2 implemented\n", encoding="utf-8")

        idx = MemoryIndex(workspace_dir=str(ws))
        idx.sync()
        results = idx.search("Selma Phase")

        assert len(results) == 1
        assert f"memory/{today}.md" == results[0].path


def test_index_search_no_results():
    """search() returns an empty list when there are no matches."""
    from selma.memory_index import MemoryIndex

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        (ws / "MEMORY.md").write_text("- Python\n", encoding="utf-8")

        idx = MemoryIndex(workspace_dir=str(ws))
        idx.sync()
        results = idx.search("QuantumPhysics")

        assert results == []


def test_index_search_min_score_filters_results():
    """min_score filters results below the threshold."""
    from selma.memory_index import MemoryIndex

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        (ws / "MEMORY.md").write_text("- Python is great\n", encoding="utf-8")

        idx = MemoryIndex(workspace_dir=str(ws))
        idx.sync()

        # min_score=0.0 → all matches pass through
        results_all = idx.search("Python", min_score=0.0)
        assert len(results_all) == 1

        # min_score=1.0 → no match (scores on small corpora ≈ 0)
        results_none = idx.search("Python", min_score=1.0)
        assert len(results_none) == 0


def test_index_max_results_limit():
    """max_results limits the number of matches."""
    from selma.memory_index import MemoryIndex

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        # 5 daily files with the same keyword
        for i in range(5):
            day = (date.today() - timedelta(days=i)).isoformat()
            (ws / "memory" / f"{day}.md").write_text(f"- Python note {i}\n", encoding="utf-8")

        idx = MemoryIndex(workspace_dir=str(ws))
        idx.sync()

        results = idx.search("Python", max_results=3)
        assert len(results) <= 3


def test_chunk_text_splits_at_paragraphs():
    """_chunk_text splits long text at paragraph boundaries."""
    from selma.memory_index import _chunk_text

    # Two clearly separated paragraphs, each well under 500 characters
    text = "Para A.\n\nPara B.\n\nPara C."
    chunks = _chunk_text(text)

    assert len(chunks) >= 1
    combined = " ".join(chunks)
    assert "Para A" in combined
    assert "Para B" in combined
    assert "Para C" in combined


def test_chunk_text_empty_input():
    """_chunk_text with empty input returns an empty list."""
    from selma.memory_index import _chunk_text

    assert _chunk_text("") == []
    assert _chunk_text("   \n\n   ") == []


def test_build_fts_query_tokenizes_words():
    """_build_fts_query produces a correct FTS5 AND expression."""
    from selma.memory_index import _build_fts_query

    q = _build_fts_query("python agent")
    assert '"python"' in q
    assert '"agent"' in q
    assert "AND" in q


def test_build_fts_query_empty_returns_empty():
    """_build_fts_query with an empty string returns an empty string."""
    from selma.memory_index import _build_fts_query

    assert _build_fts_query("") == ""
    assert _build_fts_query("   ") == ""


# ════════════════════════════════════════════════════════════
# UNIT TESTS — memory_search Tool (Phase 2)
# ════════════════════════════════════════════════════════════


def test_search_tool_returns_formatted_output():
    """memory_search tool returns formatted text with path and score."""
    from selma.tools import make_memory_search_tool

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        (ws / "MEMORY.md").write_text("- Selma is a Python framework\n", encoding="utf-8")

        tool = make_memory_search_tool(str(ws))
        result = tool.execute(query="Python")

        assert "MEMORY.md" in result
        assert "score=" in result
        assert "Python" in result


def test_search_tool_no_results_message():
    """memory_search returns a clear message when there are no matches."""
    from selma.tools import make_memory_search_tool

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        (ws / "MEMORY.md").write_text("- Python\n", encoding="utf-8")

        tool = make_memory_search_tool(str(ws))
        result = tool.execute(query="QuantumPhysics")

        assert "No" in result or "no" in result or "kein" in result.lower()


def test_search_tool_lazy_sync_on_first_call():
    """
    Sync runs on the first tool call, not at creation time.
    A file written AFTER tool creation is still findable.
    """
    from selma.tools import make_memory_search_tool

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)

        # Create tool — no files yet
        tool = make_memory_search_tool(str(ws))

        # Write file AFTER tool creation
        (ws / "MEMORY.md").write_text("- Lazy Sync Test\n", encoding="utf-8")

        # First call → sync → file is found
        result = tool.execute(query="Lazy Sync")

        assert "MEMORY.md" in result, "Lazy sync did not index the file written after tool creation"


def test_search_tool_respects_max_results():
    """max_results parameter is forwarded to the search."""
    from selma.tools import make_memory_search_tool

    with tempfile.TemporaryDirectory() as tmp:
        ws = _make_workspace(tmp)
        for i in range(5):
            day = (date.today() - timedelta(days=i)).isoformat()
            (ws / "memory" / f"{day}.md").write_text(f"- Python note {i}\n", encoding="utf-8")

        tool = make_memory_search_tool(str(ws))
        result = tool.execute(query="Python", max_results=2)

        # At most 2 matches → at most 1 "---" separator (one less than matches)
        separators = result.count("---")
        assert separators <= 1, f"Too many matches: {separators + 1} (max 2 expected)"


# ════════════════════════════════════════════════════════════
# INTEGRATION TESTS — LLM required
# ════════════════════════════════════════════════════════════

REAL_WORKSPACE = Path(".selma/workspace")


async def _integration_agent_knows_memory_md():
    """
    Agent replies with information from MEMORY.md.
    Writes temporarily to the real workspace and cleans up afterwards.
    """
    from selma.runtime import RuntimeEnv, agent_command

    memory_path = REAL_WORKSPACE / "MEMORY.md"
    original = memory_path.read_text(encoding="utf-8") if memory_path.exists() else None
    try:
        memory_path.write_text("## Known Facts\n- Favourite colour: Blue\n", encoding="utf-8")
        result = await agent_command(
            "What is my favourite colour according to MEMORY.md?",
            session_key="test:memory:injection",
            runtime=RuntimeEnv(cwd="."),
        )
        reply = result.payloads[0].text.lower()
        assert "blue" in reply or "blau" in reply, f"'Blue' not in reply: {reply[:200]}"
    finally:
        if original is not None:
            memory_path.write_text(original, encoding="utf-8")
        elif memory_path.exists():
            memory_path.unlink()


async def _integration_agent_uses_memory_get():
    """
    Agent calls memory_get when asked about memory content.
    Writes temporarily to the real workspace and cleans up afterwards.
    """
    from selma.runtime import RuntimeEnv, agent_command

    memory_path = REAL_WORKSPACE / "MEMORY.md"
    original = memory_path.read_text(encoding="utf-8") if memory_path.exists() else None
    try:
        memory_path.write_text("## Project\n- Selma is a Python agent framework\n", encoding="utf-8")
        result = await agent_command(
            "Read MEMORY.md using the memory_get tool and show me the content.",
            session_key="test:memory:get_tool",
            runtime=RuntimeEnv(cwd="."),
        )
        reply = result.payloads[0].text
        assert "Selma" in reply or "Python" in reply or "Agent" in reply, (
            f"Expected content not in reply: {reply[:200]}"
        )
    finally:
        if original is not None:
            memory_path.write_text(original, encoding="utf-8")
        elif memory_path.exists():
            memory_path.unlink()


async def _integration_agent_uses_memory_search():
    """
    Agent calls memory_search and finds content from MEMORY.md.
    Writes temporarily to the real workspace and cleans up afterwards.
    """
    from selma.runtime import RuntimeEnv, agent_command

    memory_path = REAL_WORKSPACE / "MEMORY.md"
    original = memory_path.read_text(encoding="utf-8") if memory_path.exists() else None
    try:
        memory_path.write_text(
            "## Projects\n- Selma is a Python agent framework\n",
            encoding="utf-8",
        )
        result = await agent_command(
            "Search for 'Selma' using memory_search and show me what you find.",
            session_key="test:memory:search_tool",
            runtime=RuntimeEnv(cwd="."),
        )
        reply = result.payloads[0].text
        assert "Selma" in reply or "Python" in reply or "Agent" in reply, (
            f"Expected content not in reply: {reply[:200]}"
        )
    finally:
        if original is not None:
            memory_path.write_text(original, encoding="utf-8")
        elif memory_path.exists():
            memory_path.unlink()


async def _integration_memory_flush_creates_daily_file():
    """
    memory_flush() from runtime.py writes to the daily memory file.
    Tests the real code path used by /compact and repair_context_overflow.
    """
    from selma.helper import get_workspace
    from selma.runtime import memory_flush

    today = date.today().isoformat()
    workspace_dir = get_workspace(".")
    daily_file = Path(workspace_dir) / "memory" / f"{today}.md"
    original = daily_file.read_text(encoding="utf-8") if daily_file.exists() else None

    try:
        if daily_file.exists():
            daily_file.unlink()

        await memory_flush(session_key="test:memory:flush", cwd=".")

        assert daily_file.exists(), f"Daily memory file not created by memory_flush(): {daily_file}"
        content = daily_file.read_text(encoding="utf-8")
        assert len(content.strip()) > 0, "Daily memory file is empty after flush"

    finally:
        if original is not None:
            daily_file.write_text(original, encoding="utf-8")
        elif daily_file.exists():
            daily_file.unlink()


# ════════════════════════════════════════════════════════════
# TEST RUNNER
# ════════════════════════════════════════════════════════════

UNIT_TESTS = [
    # Phase 1 — ResourceLoader
    test_memory_md_is_loaded,
    test_missing_memory_md_skipped,
    test_daily_memory_today_loaded,
    test_daily_memory_yesterday_loaded,
    test_daily_memory_both_days_loaded,
    test_empty_daily_memory_skipped,
    test_daily_memory_in_context_files,
    # Phase 1 — memory_get Tool
    test_memory_get_reads_full_file,
    test_memory_get_line_range,
    test_memory_get_daily_file,
    test_memory_get_file_not_found,
    test_memory_get_blocks_traversal,
    # Phase 2 — MemoryIndex
    test_index_sync_indexes_memory_md,
    test_index_sync_indexes_daily_files,
    test_index_no_reindex_when_unchanged,
    test_index_reindex_on_file_change,
    test_index_removes_deleted_file,
    test_index_search_returns_matching_result,
    test_index_search_finds_daily_file,
    test_index_search_no_results,
    test_index_search_min_score_filters_results,
    test_index_max_results_limit,
    test_chunk_text_splits_at_paragraphs,
    test_chunk_text_empty_input,
    test_build_fts_query_tokenizes_words,
    test_build_fts_query_empty_returns_empty,
    # Phase 2 — memory_search Tool
    test_search_tool_returns_formatted_output,
    test_search_tool_no_results_message,
    test_search_tool_lazy_sync_on_first_call,
    test_search_tool_respects_max_results,
]

INTEGRATION_TESTS = [
    # Phase 1
    _integration_agent_knows_memory_md,
    _integration_agent_uses_memory_get,
    _integration_memory_flush_creates_daily_file,
    # Phase 2
    _integration_agent_uses_memory_search,
]


@tracer.agent(name="test_unit_memory")
async def main():
    passed = 0
    failed = 0

    # ── Unit Tests ───────────────────────────────────────────
    print("=" * 54)
    print("  Unit Tests  (no LLM)")
    print("=" * 54)

    for fn in UNIT_TESTS:
        ok = _run_unit(fn.__name__, fn)
        if ok:
            passed += 1
        else:
            failed += 1

    # ── Integration Tests ────────────────────────────────────
    print()
    print("=" * 54)
    print("  Integration Tests  (Ollama required)")
    print("=" * 54)

    for fn in INTEGRATION_TESTS:
        ok = await _run_integration(fn.__name__, fn())
        if ok:
            passed += 1
        else:
            failed += 1

    # ── Summary ──────────────────────────────────────────────
    total = passed + failed
    print()
    print("=" * 54)
    print(f"  Result: {passed}/{total} passed", end="")
    print("  ✓" if failed == 0 else f"  — {failed} failed")
    print("=" * 54)


if __name__ == "__main__":
    asyncio.run(main())
