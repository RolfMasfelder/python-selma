# ============================================================
# tools.py unit tests
#
# Deckung für selma/tools.py (Tool-Factory: web_search, web_fetch,
# browser, memory_get, memory_search + create_selma_tools /
# get_tool_descriptions / ALL_TOOL_NAMES).
#
# MyTools-Tools (read/write/edit/exec/ls/grep/find) sind in
# test_unit_my_tools.py abgedeckt — hier nur die tools.py-eigenen.
#
# Abhängigkeits-Mocks (alle extern, keine echten Web/Browser-Aufrufe):
#   - tools.DDGS                       → Fake mit .text(...) (ddgs)
#   - tools.trafilatura.fetch_url/-extract
#   - tools.sync_playwright            → Fake-Sync-Playwright (SimpleNamespace)
#   - sys.modules['selma.memory_index'] → Fake get_memory_index
#     (patcht den lazy Import in tools.py, vor create_selma_tools() gesetzt)
#
# Konventionen:
#   - KEIN pytest-asyncio (sync-Tests), KEIN pytest-timeout.
#   - Isolation: SELMA_STATE_DIR-Env + <root>/.selma (autouse).
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import selma.tools as tools
from selma.agent import AgentTool


# ── Isolation (function-scope autouse) ────────────────────────
@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Dokumentiert in MEMORY.md: SELMA_STATE_DIR-Env + <root>/.selma doppelt absichern."""
    monkeypatch.setenv("SELMA_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / ".selma").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _ex(tool: AgentTool, **kwargs: Any) -> str:
    """Shortcut: tool.execute(**kwargs) — kwargs = Tool-Call-Argumente."""
    assert callable(tool.execute)
    result = tool.execute(**kwargs)
    assert isinstance(result, str)
    return result


# ════════════════════════════════════════════════════════════
# WEB SEARCH
# ════════════════════════════════════════════════════════════


class _FakeDDGS:
    """Ersetzt das von tools.py genutzte ddgs.DDGS().text(...)."""

    __slots__ = ("results", "exc", "seen_max_results")

    def __init__(self, results: Any = None, exc: Exception | None = None) -> None:
        self.results: Any = results
        self.exc = exc
        self.seen_max_results: list[int] = []

    def text(self, query: str, max_results: int = 5) -> Any:
        self.seen_max_results.append(max_results)
        if self.exc is not None:
            raise self.exc
        if self.results is None:
            raise AssertionError("FakeDDGS results not set")
        return iter(self.results)


@pytest.fixture
def fake_ddgs(monkeypatch: pytest.MonkeyPatch):
    """Erzeugt pro Test eine frange FakeDDGS-Instanz (via closure)."""
    instances: list[_FakeDDGS] = []

    def factory(results: Any = None, exc: Exception | None = None) -> _FakeDDGS:
        fake = _FakeDDGS(results, exc)

        def make() -> _FakeDDGS:
            instances.append(fake)
            return fake

        monkeypatch.setattr(tools, "DDGS", make)
        return fake

    return factory


def test_web_search_happy(fake_ddgs: Any) -> None:
    fake_ddgs(
        [
            {"title": "A", "href": "https://a", "body": "body a"},
            {"title": "B", "href": "https://b", "body": "body b"},
        ]
    )
    out = _ex(tools.make_web_search_tool(), query="x", count=2)
    assert out.startswith("1. A\n   https://a\n   body a")
    assert "2. B" in out and "body b" in out
    assert out.count("\n\n") == 1  # exakt ein Trenner zwischen den 2 Ergebnissen


def test_web_search_empty(fake_ddgs: Any) -> None:
    fake_ddgs([])
    assert _ex(tools.make_web_search_tool(), query="x") == "No results found."


def test_web_search_error(fake_ddgs: Any) -> None:
    fake_ddgs(exc=RuntimeError("network down"))
    out = _ex(tools.make_web_search_tool(), query="x")
    assert out.startswith("Error: web search failed:") and "network down" in out


def test_web_search_count_clamped_to_min_1(fake_ddgs: Any) -> None:
    fake = fake_ddgs([{"title": "t", "href": "u", "body": "b"}])
    _ex(tools.make_web_search_tool(), query="x", count=0)
    _ex(tools.make_web_search_tool(), query="x", count=-3)
    # tools.py: max(1, count) → 0/-1 schlagen als 1 nach ddgs durch
    assert fake.seen_max_results == [1, 1]


def test_web_search_missing_keys_use_empty_defaults(fake_ddgs: Any) -> None:
    # Fehlende title/href/body → r.get(...) liefert "" (leergebundener Eintrag).
    fake_ddgs([{}])
    out = _ex(tools.make_web_search_tool(), query="x")
    assert out == "1. \n   \n   "


# ════════════════════════════════════════════════════════════
# WEB FETCH
# ════════════════════════════════════════════════════════════


def test_web_fetch_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools.trafilatura, "fetch_url", lambda url: "<html>…</html>")
    monkeypatch.setattr(tools.trafilatura, "extract", lambda html: "main text")
    assert _ex(tools.make_web_fetch_tool(), url="https://x") == "main text"


def test_web_fetch_download_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str) -> str:
        raise ConnectionError("refused")

    monkeypatch.setattr(tools.trafilatura, "fetch_url", boom)
    out = _ex(tools.make_web_fetch_tool(), url="https://x")
    assert out.startswith("Error: could not fetch URL:") and "refused" in out


def test_web_fetch_empty_download(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools.trafilatura, "fetch_url", lambda url: None)
    out = _ex(tools.make_web_fetch_tool(), url="https://x")
    assert out == "Error: no content retrieved from https://x"


def test_web_fetch_empty_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools.trafilatura, "fetch_url", lambda url: "<html></html>")
    monkeypatch.setattr(tools.trafilatura, "extract", lambda html: "")
    out = _ex(tools.make_web_fetch_tool(), url="https://x")
    assert out == "Error: could not extract text from https://x"


def test_web_fetch_extract_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    # trafilatura.extract() liegt NICHT im try/except-Block von tools.py:
    # eine Exception hier schlägt ungefangen aus execute() durch (in Produktion
    # fängt agent.py Tool-Execute-Fehler ab). Verhalten dokumentieren.
    monkeypatch.setattr(tools.trafilatura, "fetch_url", lambda url: "<html></html>")

    def bad_extract(html: str) -> str:
        raise ValueError("broken parser")

    monkeypatch.setattr(tools.trafilatura, "extract", bad_extract)
    with pytest.raises(ValueError, match="broken parser"):
        _ex(tools.make_web_fetch_tool(), url="https://x")


# ════════════════════════════════════════════════════════════
# BROWSER
# ════════════════════════════════════════════════════════════


class _FakePage:
    """Ersetzt die von tools.py genutzten Playwright-Page-Methoden."""

    __slots__ = ("text", "evaluate_value", "exc", "calls")

    def __init__(
        self,
        text: str = "PAGE TEXT",
        evaluate_value: Any = None,
        exc: Exception | None = None,
    ) -> None:
        self.text = text
        self.evaluate_value = evaluate_value
        self.exc = exc
        self.calls: list[tuple] = []

    def _track(self, name: str, *args: object, **kw: object) -> None:
        self.calls.append((name, args, dict(kw)))
        if self.exc is not None:
            raise self.exc

    def goto(self, url: str, **kw: Any) -> None:
        self._track("goto", url, **kw)

    def wait_for_selector(self, selector: str, **kw: Any) -> None:
        self._track("wait_for_selector", selector, **kw)

    def inner_text(self, selector: str) -> str:
        self._track("inner_text", selector)
        return self.text

    def click(self, selector: str) -> None:
        self._track("click", selector)

    def wait_for_load_state(self, state: str) -> None:
        self._track("wait_for_load_state", state)

    def fill(self, selector: str, value: str) -> None:
        self._track("fill", selector, value)

    def evaluate(self, script: str) -> Any:
        self._track("evaluate", script)
        return self.evaluate_value

    def screenshot(self, path: Any = None, full_page: bool = False, **kw: Any) -> None:
        self._track("screenshot", path, full_page, **kw)

    def locator(self, selector: str) -> Any:
        self._track("locator", selector)

        def shot(path: Any = None) -> None:
            self.calls.append(("locator.screenshot", (selector, path), {}))

        return SimpleNamespace(screenshot=shot)


def _install_browser_fake(
    monkeypatch: pytest.MonkeyPatch,
    page: _FakePage | None = None,
    launch_exc: Exception | None = None,
) -> dict:
    """Patched tools.sync_playwright (Kontext-Manager) und gibt Zustand zum Asserten zurück."""
    pg = page or _FakePage()
    calls = {"launch": 0, "browser_closed": 0}

    def launch(headless: bool = True) -> Any:
        calls["launch"] += 1
        if launch_exc is not None:
            raise launch_exc
        return SimpleNamespace(
            new_page=lambda: pg,
            close=lambda: calls.update(browser_closed=calls["browser_closed"] + 1),
        )

    pw = SimpleNamespace(chromium=SimpleNamespace(launch=launch))

    class _PwCtx:  # tools.py nutzt `with sync_playwright() as pw:`
        def __enter__(self) -> Any:
            return pw

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(tools, "sync_playwright", lambda: _PwCtx())
    return {"page": pg, "calls": calls}


def test_browser_extract_default(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _install_browser_fake(monkeypatch, _FakePage(text="hello world"))
    out = _ex(tools.make_browser_tool("/tmp/x"), url="https://example.com")
    assert out == "hello world"
    page: _FakePage = st["page"]
    assert ("goto", ("https://example.com",), {"wait_until": "domcontentloaded", "timeout": 30_000}) in page.calls
    assert page.calls[-1][0] == "inner_text"
    assert st["calls"] == {"launch": 1, "browser_closed": 1}  # close in finally


def test_browser_extract_wait_for(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _install_browser_fake(monkeypatch, _FakePage(text="done"))
    out = _ex(
        tools.make_browser_tool("/tmp/x"),
        url="https://x",
        wait_for="#ready",
    )
    assert out == "done"
    page: _FakePage = st["page"]
    assert ("wait_for_selector", ("#ready",), {"timeout": 10_000}) in page.calls
    # wait_for wird VOR inner_text aufgerufen
    assert page.calls.index(("wait_for_selector", ("#ready",), {"timeout": 10_000})) < page.calls.index(
        ("inner_text", ("body",), {})
    )


def test_browser_extract_truncates_at_max_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    long_text = "x" * 25_000
    _install_browser_fake(monkeypatch, _FakePage(text=long_text))
    out = _ex(tools.make_browser_tool("/tmp/x"), url="https://x")
    assert out == long_text[: tools._BROWSER_MAX_CHARS] + f"\n\n[Truncated at {tools._BROWSER_MAX_CHARS} chars]"
    assert len(out) > tools._BROWSER_MAX_CHARS


def test_browser_screenshot_default_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    st = _install_browser_fake(monkeypatch)
    cwd = str(tmp_path)
    out = _ex(tools.make_browser_tool(cwd), url="https://x", action="screenshot")
    expected = f"Screenshot saved to {tmp_path / 'screenshot.png'}"
    assert out == expected
    page: _FakePage = st["page"]
    assert page.calls[-1][0] == "screenshot"
    assert page.calls[-1][1] == (str(tmp_path / "screenshot.png"), True)  # (path, full_page)


def test_browser_screenshot_selector_and_custom_path(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _install_browser_fake(monkeypatch)
    out = _ex(
        tools.make_browser_tool("/tmp/x"),
        url="https://x",
        action="screenshot",
        selector="#logo",
        screenshot_path="/tmp/shot.png",
    )
    assert out == "Screenshot saved to /tmp/shot.png"
    page: _FakePage = st["page"]
    assert ("locator", ("#logo",), {}) in page.calls
    assert page.calls[-1][0] == "locator.screenshot"
    assert page.calls[-1][1] == ("#logo", "/tmp/shot.png")
    # page.screenshot (full-page) darf hier NICHT kommen
    assert not any(c[0] == "screenshot" for c in page.calls)


def test_browser_click_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _install_browser_fake(monkeypatch, _FakePage(text="AFTER CLICK"))
    out = _ex(
        tools.make_browser_tool("/tmp/x"),
        url="https://x",
        action="click",
        selector="#btn",
    )
    assert out == "AFTER CLICK"
    page: _FakePage = st["page"]
    assert ("click", ("#btn",), {}) in page.calls
    assert ("wait_for_load_state", ("domcontentloaded",), {}) in page.calls
    # Klick vor inner_text
    assert page.calls.index(("click", ("#btn",), {})) < page.calls.index(("inner_text", ("body",), {}))


def test_browser_click_truncates_long_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_browser_fake(monkeypatch, _FakePage(text="y" * 30_000))
    out = _ex(tools.make_browser_tool("/tmp/x"), url="https://x", action="click", selector="#a")
    assert out.endswith(f"\n\n[Truncated at {tools._BROWSER_MAX_CHARS} chars]")


def test_browser_click_requires_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _install_browser_fake(monkeypatch)
    out = _ex(tools.make_browser_tool("/tmp/x"), url="https://x", action="click")
    assert out == "Error: selector required for click"
    page: _FakePage = st["page"]
    assert not any(c[0] == "click" for c in page.calls)


def test_browser_fill_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _install_browser_fake(monkeypatch)
    out = _ex(tools.make_browser_tool("/tmp/x"), url="https://x", action="fill", selector="#q", value="hallo")
    assert out == "Filled '#q' with value."
    assert ("fill", ("#q", "hallo"), {}) in st["page"].calls


def test_browser_fill_requires_selector_and_value(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _install_browser_fake(monkeypatch)
    tool = tools.make_browser_tool("/tmp/x")
    # Wert fehlt
    assert _ex(tool, url="https://x", action="fill", selector="#q") == "Error: selector and value required for fill"
    # Selector fehlt
    assert _ex(tool, url="https://x", action="fill", value="v") == "Error: selector and value required for fill"
    # Beide fehlen
    assert _ex(tool, url="https://x", action="fill") == "Error: selector and value required for fill"
    page: _FakePage = st["page"]
    assert not any(c[0] == "fill" for c in page.calls)


def test_browser_evaluate_returns_str_of_result(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _install_browser_fake(monkeypatch, _FakePage(evaluate_value={"a": 1}))
    out = _ex(tools.make_browser_tool("/tmp/x"), url="https://x", action="evaluate", script="1+1")
    assert out == "{'a': 1}"  # tools.py führt str(result) aus
    assert ("evaluate", ("1+1",), {}) in st["page"].calls
    page2: _FakePage = st["page"]
    # evaluate (dict) liefert das ROHE Resultat, str()-Wicklung passiert in tools.py
    assert page2.evaluate_value == {"a": 1}


def test_browser_evaluate_requires_script(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _install_browser_fake(monkeypatch)
    out = _ex(tools.make_browser_tool("/tmp/x"), url="https://x", action="evaluate")
    assert out == "Error: script required for evaluate"
    assert not any(c[0] == "evaluate" for c in st["page"].calls)


def test_browser_unknown_action(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _install_browser_fake(monkeypatch)
    out = _ex(tools.make_browser_tool("/tmp/x"), url="https://x", action="teleport")
    assert out == "Error: unknown action 'teleport'"
    # navigation hat stattgefunden, keine Aktion wurde ausgeführt
    page: _FakePage = st["page"]
    assert page.calls and page.calls[0][0] == "goto"
    assert st["calls"]["browser_closed"] == 1


def test_browser_page_error_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    st = _install_browser_fake(monkeypatch, _FakePage(exc=RuntimeError("page exploded")))
    out = _ex(tools.make_browser_tool("/tmp/x"), url="https://x")
    assert out == "Error: page exploded"
    # try/except/finally: browser.close() wird trotzdem aufgerufen
    assert st["calls"]["browser_closed"] == 1


def test_browser_launch_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    # launch() liegt AUSSERHALB des try/except-Blocks in tools.py → Exception
    # schlägt ungefangen aus execute() (in Produktion fängt agent.py ab).
    _install_browser_fake(monkeypatch, launch_exc=RuntimeError("no chromium binary"))
    with pytest.raises(RuntimeError, match="no chromium binary"):
        _ex(tools.make_browser_tool("/tmp/x"), url="https://x")


# ════════════════════════════════════════════════════════════
# MEMORY GET
#
# memory_get liest ECHTE Dateien aus <cwd>/.selma/workspace/ —
# kein Mock, sondern echte temp-Dateien (klein, schnell).
# ════════════════════════════════════════════════════════════


def _write_mem(tmp_path: Path, rel: str, content: str) -> Path:  #
    ws = tmp_path / ".selma" / "workspace"
    target = ws / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return ws


def test_memory_get_happy(tmp_path: Path) -> None:
    _write_mem(tmp_path, "MEMORY.md", "line1\nline2\nline3")
    out = _ex(tools.make_memory_get_tool(str(tmp_path)), path="MEMORY.md")
    assert out == "line1\nline2\nline3"


def test_memory_get_subdir(tmp_path: Path) -> None:
    _write_mem(tmp_path, "memory/2026-09-08.md", "daily entry")
    out = _ex(tools.make_memory_get_tool(str(tmp_path)), path="memory/2026-09-08.md")
    assert out == "daily entry"


def test_memory_get_from_line(tmp_path: Path) -> None:
    _write_mem(tmp_path, "MEMORY.md", "a\nb\nc\nd")
    tool = tools.make_memory_get_tool(str(tmp_path))
    # read uses splitlines(keepends=True) — trailing newlines are preserved
    assert _ex(tool, path="MEMORY.md", from_line=2) == "b\nc\nd"
    assert _ex(tool, path="MEMORY.md", from_line=3, lines=1) == "c\n"
    # from_line=0 → gesamter Inhalt (start=0)
    assert _ex(tool, path="MEMORY.md", from_line=0) == "a\nb\nc\nd"
    # from_line über Datei-Ende → leer
    assert _ex(tool, path="MEMORY.md", from_line=99) == "(empty)"
    # lines ohne from_line → erste N Zeilen
    assert _ex(tool, path="MEMORY.md", lines=2) == "a\nb\n"


def test_memory_get_empty_file(tmp_path: Path) -> None:
    _write_mem(tmp_path, "empty.md", "")
    assert _ex(tools.make_memory_get_tool(str(tmp_path)), path="empty.md") == "(empty)"


def test_memory_get_truncates_at_max_lines(tmp_path: Path) -> None:
    n = tools._MEMORY_MAX_LINES + 50  # 550 Zeilen
    _write_mem(tmp_path, "big.md", "\n".join(f"line{i}" for i in range(n)))
    out = _ex(tools.make_memory_get_tool(str(tmp_path)), path="big.md")
    # 500 Zeilen (je 1 \n) + Marker-Zeile (1 \n) = 501
    assert out.count("\n") == tools._MEMORY_MAX_LINES + 1
    assert out.endswith(f"\n[Truncated at {tools._MEMORY_MAX_LINES} lines]")
    # die letzten beiden enthaltenen Zeilen sind line499… — alles danach fehlt
    assert "line499\n" in out
    assert "line500" not in out  # darf nicht enthalten sein (auch keine Präfix-Teilmengerei)
    assert out.startswith("line0\n")


def test_memory_get_path_escape_rejected(tmp_path: Path) -> None:
    # Außerhalb des workspace-Roots → abgewiesen
    outsider = tmp_path / "outside.md"
    outsider.write_text("secret", encoding="utf-8")
    tool = tools.make_memory_get_tool(str(tmp_path))
    assert _ex(tool, path="../../outside.md") == "Error: path must be within the workspace."
    assert _ex(tool, path="../outside.md") == "Error: path must be within the workspace."


def test_memory_get_file_not_found(tmp_path: Path) -> None:
    # Workspace-Verzeichnis existiert gar nicht → Datei fehlt
    assert _ex(tools.make_memory_get_tool(str(tmp_path)), path="MEMORY.md") == "File not found: MEMORY.md"


# ════════════════════════════════════════════════════════════
# MEMORY SEARCH
#
# tools.py importiert selma.memory_index LAZY im Funktionskörper —
# per sys.modules-Fake abgefangen (ersetzt das echte Modul).
# ════════════════════════════════════════════════════════════


class _FakeIndex:
    def __init__(self) -> None:
        self.sync_calls = 0
        self.search_calls: list[tuple] = []
        self.results: list[Any] = []

    def sync(self) -> int:
        self.sync_calls += 1
        return 3

    def search(self, query: str, max_results: int = 10, min_score: float | None = None) -> list[Any]:
        self.search_calls.append((query, max_results, min_score))
        return self.results


@pytest.fixture
def fake_memory_index(monkeypatch: pytest.MonkeyPatch) -> dict:
    index = _FakeIndex()
    captured: dict[str, Any] = {}

    def get_memory_index(cwd: str, **kw: Any) -> Any:
        captured["cwd"] = cwd
        captured["kw"] = dict(kw)
        return index

    fake_mod = SimpleNamespace(get_memory_index=get_memory_index)
    monkeypatch.setitem(sys.modules, "selma.memory_index", fake_mod)
    return {"index": index, "captured": captured}


def test_memory_search_no_results(fake_memory_index: dict) -> None:
    out = _ex(tools.make_memory_search_tool("/ws"), query="selma")
    assert out == "No memory results found for: 'selma'"
    index: _FakeIndex = fake_memory_index["index"]
    assert index.sync_calls == 1  # lazy: exakt einmal beim ersten Aufruf
    assert index.search_calls == [("selma", 10, None)]


def test_memory_search_lazy_sync_once(fake_memory_index: dict) -> None:
    tool = tools.make_memory_search_tool("/ws")
    _ex(tool, query="a")
    _ex(tool, query="b")
    index: _FakeIndex = fake_memory_index["index"]
    assert index.sync_calls == 1
    assert [c[0] for c in index.search_calls] == ["a", "b"]


def test_memory_search_result_format(fake_memory_index: dict) -> None:
    index: _FakeIndex = fake_memory_index["index"]
    index.results = [
        SimpleNamespace(path="memory/2026-01-01.md", score=0.876, content="first hit"),
        SimpleNamespace(path="MEMORY.md", score=0.42, content="second hit"),
    ]
    out = _ex(tools.make_memory_search_tool("/ws"), query="x")
    assert "[memory/2026-01-01.md]  score=0.88\nfirst hit" in out
    assert "[MEMORY.md]  score=0.42\nsecond hit" in out
    assert out.count("\n\n---\n\n") == 1  # Joiner zwischen den 2 Treffern


def test_memory_search_params_passthrough(fake_memory_index: dict) -> None:
    _ex(tools.make_memory_search_tool("/ws"), query="q", max_results=3, min_score=0.5)
    assert fake_memory_index["index"].search_calls == [("q", 3, 0.5)]


def test_memory_search_defaults_without_config(fake_memory_index: dict) -> None:
    assert tools.make_memory_search_tool("/some/workspace").name == "memory_search"
    assert fake_memory_index["captured"]["cwd"] == "/some/workspace"
    assert fake_memory_index["captured"]["kw"] == {
        "vector_search": False,
        "embed_model": "nomic-embed-text",
        "embed_base_url": "http://localhost:11434/v1",
        "temporal_decay": False,
        "temporal_decay_rate": 0.01,
    }


def test_memory_search_config_values_propagated(fake_memory_index: dict) -> None:
    cfg = SimpleNamespace(
        memory=SimpleNamespace(
            vector_search=True,
            embed_model="mxbai-embed-large",
            temporal_decay=True,
            temporal_decay_rate=0.02,
        ),
        model=SimpleNamespace(ollama_base_url="http://10.0.0.5:11434/v1"),
    )
    assert tools.make_memory_search_tool("/ws", config=cfg).name == "memory_search"
    assert fake_memory_index["captured"]["kw"] == {
        "vector_search": True,
        "embed_model": "mxbai-embed-large",
        "embed_base_url": "http://10.0.0.5:11434/v1",
        "temporal_decay": True,
        "temporal_decay_rate": 0.02,
    }


# ════════════════════════════════════════════════════════════
# TOOL-REGISTRIERUNG (PUBLIC API)
# ════════════════════════════════════════════════════════════


def test_all_tool_names_exact() -> None:
    assert tools.ALL_TOOL_NAMES == [
        "read",
        "write",
        "edit",
        "exec",
        "ls",
        "grep",
        "find",
        "web_search",
        "web_fetch",
        "browser",
        "memory_get",
        "memory_search",
    ]


def test_create_selma_tools_names_match_and_shape(fake_memory_index: dict) -> None:
    tool_list = tools.create_selma_tools("/tmp/anywhere")
    assert [t.name for t in tool_list] == tools.ALL_TOOL_NAMES
    assert len(tool_list) == 12
    for t in tool_list:
        assert t.name
        assert isinstance(t.description, str) and t.description
        assert t.parameters is not None
        assert callable(t.execute)


def test_create_selma_tools_with_config(fake_memory_index: dict) -> None:
    cfg = SimpleNamespace(
        memory=SimpleNamespace(
            vector_search=True,
            embed_model="e2",
            temporal_decay=True,
            temporal_decay_rate=0.03,
        ),
        model=SimpleNamespace(ollama_base_url="http://cfg:11434/v1"),
    )
    tool_list = tools.create_selma_tools("/tmp/fw", config=cfg)
    assert [t.name for t in tool_list] == tools.ALL_TOOL_NAMES
    # die Konfiguration erreicht get_memory_index:
    assert fake_memory_index["captured"]["cwd"] == "/tmp/fw"
    assert fake_memory_index["captured"]["kw"]["vector_search"] is True
    assert fake_memory_index["captured"]["kw"]["embed_model"] == "e2"
    assert fake_memory_index["captured"]["kw"]["embed_base_url"] == "http://cfg:11434/v1"


def test_get_tool_descriptions(fake_memory_index: dict) -> None:
    d = tools.get_tool_descriptions()
    assert set(d) == set(tools.ALL_TOOL_NAMES)
    assert all(isinstance(v, str) and v for v in d.values())
    assert "DuckDuckGo" in d["web_search"]
    assert "browser" in d["browser"].lower() or "Chromium" in d["browser"]
