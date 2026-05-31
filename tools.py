# ============================================================
# tools.py
#
# Tool factory for Selma.
# Contains all coding tools from my_mono/tools.py
# plus additional tools currently available to Selma.
#
# Currently available: web_search, web_fetch, browser
# ============================================================

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import trafilatura
from ddgs import DDGS
from playwright.sync_api import sync_playwright

from my_mono.agent import AgentTool, ToolSchema
from my_mono.tools import (
    _make_edit_tool,
    _make_find_tool,
    _make_grep_tool,
    _make_ls_tool,
    _make_read_tool,
    _make_write_tool,
)

if TYPE_CHECKING:
    from config import SelmaConfig


# ─── WEB SEARCH ──────────────────────────────────────────────

def _make_web_search_tool() -> AgentTool:
    """Web search via DuckDuckGo (ddgs)."""

    def execute(query: str, count: int = 5, **_) -> str:
        try:
            results = list(DDGS().text(query, max_results=max(1, count)))
        except Exception as e:
            return f"Error: web search failed: {e}"

        if not results:
            return "No results found."

        lines: list[str] = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            url = r.get("href", "")
            body = r.get("body", "")
            lines.append(f"{i}. {title}\n   {url}\n   {body}")

        return "\n\n".join(lines)

    return AgentTool(
        name="web_search",
        description="Search the web via DuckDuckGo. Returns titles, URLs, and descriptions.",
        parameters=ToolSchema(
            properties={
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5)",
                },
            },
            required=["query"],
        ),
        execute=execute,
    )


# ─── WEB FETCH ───────────────────────────────────────────────

def _make_web_fetch_tool() -> AgentTool:
    """Fetch and extract the main text content of a URL using trafilatura."""

    def execute(url: str, **_) -> str:
        try:
            downloaded = trafilatura.fetch_url(url)
        except Exception as e:
            return f"Error: could not fetch URL: {e}"

        if not downloaded:
            return f"Error: no content retrieved from {url}"

        text = trafilatura.extract(downloaded)
        if not text:
            return f"Error: could not extract text from {url}"

        return text

    return AgentTool(
        name="web_fetch",
        description=(
            "Fetch a URL and extract its main readable text content. "
            "Strips navigation, ads, and boilerplate."
        ),
        parameters=ToolSchema(
            properties={
                "url": {
                    "type": "string",
                    "description": "URL to fetch",
                },
            },
            required=["url"],
        ),
        execute=execute,
    )


# ─── BROWSER ─────────────────────────────────────────────────
#
# Requires Chromium to be installed separately:
#   uv run playwright install chromium
#
# Actions:
#   Action       | Required params        | Returns
#   -------------|------------------------|---------------------------
#   extract      | url                    | visible page text (default)
#   screenshot   | url                    | path to saved PNG
#   click        | url, selector          | updated page text
#   fill         | url, selector, value   | confirmation message
#   evaluate     | url, script            | JavaScript result
#
# Optional for all actions:
#   wait_for        – CSS selector to wait for before acting
#   screenshot_path – custom save path (screenshot action)

_BROWSER_MAX_CHARS = 20_000


def _make_browser_tool(cwd: str) -> AgentTool:
    """
    Headless Chromium via Playwright.

    Actions:
      extract    – navigate and return visible text (default)
      screenshot – save a screenshot, return the file path
      click      – click a CSS selector, return updated page text
      fill       – fill a form field (selector + value)
      evaluate   – run JavaScript and return the result
    """

    def execute(
        url: str,
        action: str = "extract",
        selector: str | None = None,
        value: str | None = None,
        script: str | None = None,
        screenshot_path: str | None = None,
        wait_for: str | None = None,
        **_,
    ) -> str:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)

                if wait_for:
                    page.wait_for_selector(wait_for, timeout=10_000)

                if action == "extract":
                    text = page.inner_text("body")
                    if len(text) > _BROWSER_MAX_CHARS:
                        text = text[:_BROWSER_MAX_CHARS] + f"\n\n[Truncated at {_BROWSER_MAX_CHARS} chars]"
                    return text

                if action == "screenshot":
                    path = screenshot_path or os.path.join(cwd, "screenshot.png")
                    if selector:
                        page.locator(selector).screenshot(path=path)
                    else:
                        page.screenshot(path=path, full_page=True)
                    return f"Screenshot saved to {path}"

                if action == "click":
                    if not selector:
                        return "Error: selector required for click"
                    page.click(selector)
                    page.wait_for_load_state("domcontentloaded")
                    text = page.inner_text("body")
                    if len(text) > _BROWSER_MAX_CHARS:
                        text = text[:_BROWSER_MAX_CHARS] + f"\n\n[Truncated at {_BROWSER_MAX_CHARS} chars]"
                    return text

                if action == "fill":
                    if not selector or value is None:
                        return "Error: selector and value required for fill"
                    page.fill(selector, value)
                    return f"Filled '{selector}' with value."

                if action == "evaluate":
                    if not script:
                        return "Error: script required for evaluate"
                    result = page.evaluate(script)
                    return str(result)

                return f"Error: unknown action '{action}'"

            except Exception as e:
                return f"Error: {e}"
            finally:
                browser.close()

    return AgentTool(
        name="browser",
        description=(
            "Control a headless Chromium browser via Playwright. "
            "Actions: extract (default, returns page text), screenshot (saves PNG), "
            "click (clicks a CSS selector), fill (fills a form field), "
            "evaluate (runs JavaScript)."
        ),
        parameters=ToolSchema(
            properties={
                "url": {
                    "type": "string",
                    "description": "URL to navigate to",
                },
                "action": {
                    "type": "string",
                    "description": "Action: extract | screenshot | click | fill | evaluate (default: extract)",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector for click, fill, or element screenshot",
                },
                "value": {
                    "type": "string",
                    "description": "Value to fill into a form field (fill action)",
                },
                "script": {
                    "type": "string",
                    "description": "JavaScript expression to evaluate (evaluate action)",
                },
                "screenshot_path": {
                    "type": "string",
                    "description": "File path to save screenshot (default: <cwd>/screenshot.png)",
                },
                "wait_for": {
                    "type": "string",
                    "description": "CSS selector to wait for before executing the action",
                },
            },
            required=["url"],
        ),
        execute=execute,
    )


# ─── MEMORY GET ──────────────────────────────────────────────

_MEMORY_MAX_LINES = 500


def _make_memory_get_tool(cwd: str) -> AgentTool:
    """Read a file from the memory workspace (MEMORY.md or memory/YYYY-MM-DD.md).

    cwd is the workspace directory itself (e.g. .selma/workspace), not the
    project root. This is consistent with how runtime.py passes workspace_dir
    to create_selma_tools().
    """

    memory_dir = cwd

    def execute(path: str, from_line: int = 0, lines: int = 0, **_) -> str:
        from pathlib import Path as _Path
        # Resolve against workspace and verify the result stays inside it
        full_path = (_Path(memory_dir) / path).resolve()
        workspace_root = _Path(memory_dir).resolve()
        if not full_path.is_relative_to(workspace_root):
            return "Error: path must be within the workspace."

        try:
            text = open(full_path, encoding="utf-8").read()
        except FileNotFoundError:
            return f"File not found: {path}"
        except Exception as e:
            return f"Error reading file: {e}"

        all_lines = text.splitlines(keepends=True)

        start = max(0, from_line - 1) if from_line > 0 else 0
        if lines > 0:
            all_lines = all_lines[start : start + lines]
        elif start > 0:
            all_lines = all_lines[start:]

        if len(all_lines) > _MEMORY_MAX_LINES:
            all_lines = all_lines[:_MEMORY_MAX_LINES]
            all_lines.append(f"\n[Truncated at {_MEMORY_MAX_LINES} lines]")

        return "".join(all_lines) if all_lines else "(empty)"

    return AgentTool(
        name="memory_get",
        description=(
            "Read a memory file from the workspace. "
            "Use for MEMORY.md or daily notes (memory/YYYY-MM-DD.md). "
            "Supports optional line range via from_line and lines."
        ),
        parameters=ToolSchema(
            properties={
                "path": {
                    "type": "string",
                    "description": "File path relative to workspace, e.g. 'MEMORY.md' or 'memory/2026-05-12.md'",
                },
                "from_line": {
                    "type": "integer",
                    "description": "Start at this line number (1-based, default: 1)",
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of lines to read (default: all)",
                },
            },
            required=["path"],
        ),
        execute=execute,
    )


# ─── MEMORY SEARCH ───────────────────────────────────────────

def _make_memory_search_tool(cwd: str, config: "SelmaConfig | None" = None) -> AgentTool:
    """
    FTS5 full-text search over all memory files.
    When config.memory.vector_search is True: hybrid FTS5 + cosine similarity.

    Lazy sync: the index is built (or updated) on the first call
    within a session. Only changed files are re-indexed.

    cwd is the workspace directory (same convention as memory_get).
    """
    from memory_index import get_memory_index

    vector_search = False
    embed_model = "nomic-embed-text"
    embed_base_url = "http://localhost:11434/v1"
    temporal_decay = False
    temporal_decay_rate = 0.01

    if config is not None:
        vector_search = config.memory.vector_search
        embed_model = config.memory.embed_model
        embed_base_url = config.model.ollama_base_url
        temporal_decay = config.memory.temporal_decay
        temporal_decay_rate = config.memory.temporal_decay_rate

    index = get_memory_index(
        cwd,
        vector_search=vector_search,
        embed_model=embed_model,
        embed_base_url=embed_base_url,
        temporal_decay=temporal_decay,
        temporal_decay_rate=temporal_decay_rate,
    )
    _synced: list[bool] = [False]   # mutable cell for the closure

    def execute(
        query: str,
        max_results: int = 10,
        min_score: float | None = None,
        **_,
    ) -> str:
        if not _synced[0]:
            n = index.sync()
            _synced[0] = True
            if n:
                pass  # logged inside sync()

        results = index.search(query, max_results=max_results, min_score=min_score)

        if not results:
            return f"No memory results found for: {query!r}"

        lines: list[str] = []
        for r in results:
            lines.append(f"[{r.path}]  score={r.score:.2f}\n{r.content}")

        return "\n\n---\n\n".join(lines)

    return AgentTool(
        name="memory_search",
        description=(
            "Search MEMORY.md and daily memory notes using full-text search. "
            "Use when looking for past decisions, facts, or notes by topic. "
            "Prefer memory_get when you know the exact file to read."
        ),
        parameters=ToolSchema(
            properties={
                "query": {
                    "type": "string",
                    "description": "Search query — keywords or a short phrase",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default: 10)",
                },
                "min_score": {
                    "type": "number",
                    "description": "Minimum relevance score 0..1 (optional)",
                },
            },
            required=["query"],
        ),
        execute=execute,
    )


# ─── PUBLIC API ───────────────────────────────────────────────

ALL_TOOL_NAMES: list[str] = [
    "read", "write", "edit", "ls", "grep", "find",
    "web_search", "web_fetch", "browser",
    "memory_get", "memory_search",
]


def get_tool_descriptions() -> dict[str, str]:
    """Returns {tool_name: description} for all Selma tools."""
    return {tool.name: tool.description for tool in create_selma_tools(".")}


def create_selma_tools(cwd: str, config: "SelmaConfig | None" = None) -> list[AgentTool]:
    """
    All tools currently available to Selma:
      - read, write, edit, ls, grep, find  (from my_mono/tools.py)
      - web_search, web_fetch, browser
      - memory_get, memory_search
    """
    return [
        _make_read_tool(cwd),
        _make_write_tool(cwd),
        _make_edit_tool(cwd),
        _make_ls_tool(cwd),
        _make_grep_tool(cwd),
        _make_find_tool(cwd),
        _make_web_search_tool(),
        _make_web_fetch_tool(),
        _make_browser_tool(cwd),
        _make_memory_get_tool(cwd),
        _make_memory_search_tool(cwd, config=config),
    ]
