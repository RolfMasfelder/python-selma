# ============================================================
# test_function_call.py
#
# Tests all locally available Ollama models for function-calling
# (tool use) support via the OpenAI-compatible endpoint.
#
# For each model a simple prompt is sent together with one tool
# definition.  If the model responds with finish_reason="tool_calls"
# and a non-empty tool_calls list the test passes.
#
# Usage:
#   uv run test_function_call.py
#   uv run test_function_call.py --base-url http://localhost:11434/v1
#   uv run test_function_call.py --timeout 30
# ============================================================

import argparse
import asyncio
import json
import sys
import urllib.request

from openai import AsyncOpenAI

OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
DEFAULT_TIMEOUT  = 60   # seconds per model

TEST_TOOL = {
    "type": "function",
    "function": {
        "name": "get_current_weather",
        "description": "Get the current weather in a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "The city name, e.g. Berlin",
                },
            },
            "required": ["city"],
        },
    },
}

TEST_PROMPT = "What is the weather like in Berlin right now? Use the available tool."


# ── Ollama model discovery ─────────────────────────────────────────────────────

def list_ollama_models(tags_url: str) -> list[str]:
    """Returns model names from the Ollama /api/tags endpoint."""
    try:
        with urllib.request.urlopen(tags_url, timeout=5) as resp:
            data = json.loads(resp.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception as e:
        print(f"  Error contacting Ollama at {tags_url}: {e}", file=sys.stderr)
        sys.exit(1)


# ── Single model test ──────────────────────────────────────────────────────────

async def _probe_model(client: AsyncOpenAI, model: str, timeout: int) -> dict:
    """
    Returns a dict with keys:
      model, supports_tools (bool), finish_reason (str), tool_name (str|None),
      error (str|None)
    """
    result = {
        "model": model,
        "supports_tools": False,
        "finish_reason": None,
        "tool_name": None,
        "error": None,
    }

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": TEST_PROMPT}],
                tools=[TEST_TOOL],
                stream=False,
            ),
            timeout=timeout,
        )

        choice = response.choices[0]
        result["finish_reason"] = choice.finish_reason

        tool_calls = getattr(choice.message, "tool_calls", None) or []
        if tool_calls and choice.finish_reason == "tool_calls":
            result["supports_tools"] = True
            result["tool_name"] = tool_calls[0].function.name

    except asyncio.TimeoutError:
        result["error"] = f"timeout after {timeout}s"
    except Exception as e:
        result["error"] = str(e)[:120]

    return result


# ── Reporting ──────────────────────────────────────────────────────────────────

def _pad(text: str, width: int) -> str:
    return text[:width].ljust(width)


def print_results(results: list[dict]) -> None:
    col_model  = max(len(r["model"]) for r in results) + 2
    col_model  = max(col_model, 8)

    header = (
        _pad("Model", col_model)
        + _pad("Tools", 8)
        + _pad("finish_reason", 16)
        + _pad("Called tool", 24)
        + "Error"
    )
    sep = "─" * len(header)

    print()
    print(header)
    print(sep)

    for r in results:
        status   = "✅ yes" if r["supports_tools"] else "❌ no "
        finish   = r["finish_reason"] or "-"
        called   = r["tool_name"]     or "-"
        error    = r["error"]         or ""
        print(
            _pad(r["model"], col_model)
            + _pad(status, 8)
            + _pad(finish, 16)
            + _pad(called, 24)
            + error
        )

    print(sep)
    supported = [r["model"] for r in results if r["supports_tools"]]
    print(f"\n  {len(supported)}/{len(results)} models support function calling.")
    if supported:
        print("  Supported: " + ", ".join(supported))
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(base_url: str, timeout: int) -> None:
    print(f"\n  Ollama function-call probe")
    print(f"  endpoint : {base_url}")
    print(f"  tool     : {TEST_TOOL['function']['name']}")
    print(f"  timeout  : {timeout}s per model")

    models = list_ollama_models(OLLAMA_TAGS_URL)
    if not models:
        print("  No models found.", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Found {len(models)} model(s). Testing ...\n")

    client  = AsyncOpenAI(base_url=base_url, api_key="ollama")
    results = []

    for model in models:
        print(f"  Testing {model} ...", end="", flush=True)
        r = await _probe_model(client, model, timeout)
        mark = "✅" if r["supports_tools"] else ("⏱" if r["error"] else "❌")
        print(f"\r  {mark}  {model:<50}", flush=True)
        results.append(r)

    print_results(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Ollama models for function-calling support.")
    parser.add_argument("--base-url", default=OLLAMA_BASE_URL, help="Ollama OpenAI-compatible base URL")
    parser.add_argument("--timeout",  type=int, default=DEFAULT_TIMEOUT, help="Seconds to wait per model")
    args = parser.parse_args()

    asyncio.run(main(args.base_url, args.timeout))
