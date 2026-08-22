# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Create and activate the virtual environment (once)
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate.bat

# Install Selma in editable mode + dev dependencies
pip install -e ".[dev]"

# Activate the pre-commit git hook (ruff lint + format + hygiene checks)
pre-commit install

# Run all checks manually
pre-commit run --all-files

# Initialize workspace and copy default skills
python -m selma.setup

# Install browser tool support
playwright install chromium

# Run the gateway (REST API on :8000)
python -m selma.gateway

# Run all pytest-based unit tests
pytest

# Run integration test scripts individually
python tests/test_runtime.py
python tests/test_agent_session_chat.py   # interactive CLI chat
python tests/test_webchat.py
python tests/test_skills.py
python tests/test_function_call.py
```

Every terminal used for this project must have `venv` activated first (`source venv/bin/activate`).

## Architecture

Selma is an agentic AI gateway that routes user messages from multiple channels (WebChat, Telegram) through a three-layer agent engine and delivers streamed responses back.

### Three-Layer Agent Engine (`src/selma/runtime.py`)

```
Layer 1: agent_command()          — orchestration: who, which model, which session
Layer 2: run_embedded_pi_agent()  — robustness: retry loop, context overflow, compaction
Layer 3: run_embedded_attempt()   — execution: build system prompt, create session, invoke tools
```

Error classification (`detect_attempt_error`) drives automatic recovery:
- `context_overflow` → compact session → retry (max 3 attempts)
- `thinking_not_supported` → lower thinking level → retry
- `aborted` → surface to caller

### Message Flow

```
User (Telegram / WebChat)
  → gateway.py (FastAPI)
      → CommandManager (/commands)
      → process_message_flow()
          → normalize() → NormalizedTurnInput
          → runtime.agent_command()  (three layers)
          → DeliveryContext callbacks → channel output
```

All of the above live in `src/selma/` and are imported as `selma.<module>` (e.g. `from selma.runtime import agent_command`).

### Session Persistence

Sessions are keyed by `session_key` and stored in `~/.selma/agents/main/sessions/` (or `.selma/` in cwd, or `$SELMA_STATE_DIR`):
- `sessions.json` — map of session_key → SessionRecord (model, thinking level, skills snapshot, last interaction)
- `<session_id>.jsonl` — full transcript, managed by `selma.agent_session`

Sessions reset daily at a configured hour or after idle timeout.

### System Prompt Assembly (`system_prompt.py`)

Built per turn from workspace context files loaded in order:
`AGENTS.md → SOUL.md → IDENTITY.md → USER.md → TOOLS.md → MEMORY.md → HEARTBEAT.md → daily memory → BOOTSTRAP.md`

Plus: skills snapshots (SKILL.md files, version-hashed and cached in SessionRecord), active tool names, runtime info (model, channel, host).

### Channel Adapters

`channel_adapter.py` defines the `ChannelAdapter` protocol. Implementations:
- `adapter_webchat.py` — Server-Sent Events streaming via asyncio queue
- `adapter_telegram.py` — Telegram bot via python-telegram-bot

### Key Components

| File | Role |
|------|------|
| `src/selma/gateway.py` | FastAPI app, SSE endpoints, heartbeat lifespan |
| `src/selma/runtime.py` | Three-layer engine, session management, error recovery |
| `src/selma/config.py` | Pydantic models loading `.selma/selma.json` |
| `src/selma/session_store.py` | SessionRecord persistence |
| `src/selma/system_prompt.py` | System prompt builder |
| `src/selma/tools.py` | Web search (DuckDuckGo), web fetch (trafilatura), browser (Playwright), file tools |
| `src/selma/skills.py` | SKILL.md loading and version hashing |
| `src/selma/heartbeat.py` | Scheduled proactive agent turns |
| `src/selma/compaction.py` | Session compression via LLM summarization |
| `src/selma/delivery.py` | Output callbacks (on_partial_reply, on_block_reply, on_tool_call) |
| `src/selma/resource_loader.py` | Workspace context file loading |
| `src/selma/agent.py`, `agent_session.py` | Core agent primitives: AgentSession, streaming, tool execution |
| `src/selma/my_tools.py` | Generic coding tools (read/write/edit/ls/grep/find) used by AgentSession |

### Streaming / Block Chunking

Text output is split into blocks by `_BlockChunker`: buffers tokens until ≥ min chars and a flush pattern (sentence boundary) is reached, then fires `on_block_reply`. End of turn fires `on_block_reply_flush`.

### Configuration (`.selma/selma.json`)

Key settings: `model` (provider/model-id), `channels` (telegram, webchat), `heartbeat` (every, target, light_context, isolated_session), `session.reset` (at_hour, idle_minutes), `tools_allow` (all or list), `memory` (vector_search, embed_model).

### Tracing

OpenTelemetry via `selma.tracing`. Decorate key async functions with `@tracer.chain()` or `@tracer.agent()`. Phoenix collector on `localhost:6006` (no-op if unavailable).

## Conventions

- All Python comments, docstrings, and Pydantic Field descriptions must be in English.
- The installable package lives in `src/selma/`; tests live in `tests/`. Files prefixed `my_` (`my_tools.py`, `my_resource_loader.py`, `my_system_prompt.py`) hold the generic, low-level agent primitives, kept apart from Selma-specific modules of the same concern (`tools.py`, `resource_loader.py`, `system_prompt.py`).
- Use the project's `venv` for every terminal (`source venv/bin/activate`) and run scripts with plain `python`/`pytest`, never `uv`.
- Async-first: all agent operations use `asyncio`.
- Pydantic v2 `BaseModel` with `model_config` for serialization settings.
- Tool filtering: `tools_allow` in config controls which tools are injected per session; filter happens at runtime, not at tool creation.
- Bootstrap mode: if `BOOTSTRAP.md` has content, the first agent turn uses it as a special setup prompt, then clears the file.
- Lint/format: `ruff` (config in `pyproject.toml`), enforced via `pre-commit`. Run `pre-commit run --all-files` before committing if the hook isn't installed.
