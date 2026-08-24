
# Agent Selma 👩🏻

## A "Toy" Implementation of OpenClaw with Python

```html
<p align="center">
  <img src="images/selma_portrait.png" width="300" alt="Agent Selma interacting with futuristic holographic data displays." />
</p>
```

## 🌟 Overview

**Agent Selma** is a simplified, educational reimplementation of the [OpenClaw](https://github.com/openclaw/openclaw) project.

The primary goal is to deconstruct and understand the underlying architecture of autonomous agents by rebuilding them from scratch using **Python**. By stripping away complexity, Selma serves as a clean baseline for learning how agentic components interact.

> [!WARNING]
> **This is a toy project for learning purposes only.**
> Selma is not hardened for production use. There are no security audits, no SLA guarantees, and no guarantees of stability or correctness. **Do not use this in any production or commercial environment.** Use at your own risk.

## 🛠 Tech Stack

| Component | Library |
|---|---|
| Language | [Python 3.13+](https://www.python.org/) |
| LLM API | [Ollama](https://ollama.com/) (local) or any OpenAI-compatible API |
| Agent Framework | [Pydantic AI](https://ai.pydantic.dev/) — see [why it wasn't used](doc/pydantic_ai.md) |
| Gateway | [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) |
| Dashboard | [Streamlit](https://streamlit.io/) |
| Telegram Channel | [python-telegram-bot](https://python-telegram-bot.org/) |
| Tracing | [OpenTelemetry](https://opentelemetry.io/) + [Arize Phoenix](https://phoenix.arize.com/) |
| Web tools | [Playwright](https://playwright.dev/python/), [Trafilatura](https://trafilatura.readthedocs.io/), [DDGS](https://github.com/deedy5/duckduckgo_search) |
| Inspiration | [OpenClaw](https://github.com/openclaw/openclaw) / [PI-Agent](https://github.com/badlogic/pi-mono) |

## 🗂️ Project Structure

```txt
src/selma/          ← installable "selma" package (the gateway/runtime code)
    agent.py, agent_session.py ← low-level agent primitives (Agent, AgentSession)
    my_tools.py, my_resource_loader.py, my_system_prompt.py ← generic counterparts of tools.py/resource_loader.py/system_prompt.py
tests/               ← pytest suite + standalone integration scripts (see "Test Scripts" below)
skills/              ← SKILL.md folders deployed into the workspace by `python -m selma.setup`
templates/           ← default workspace context files (AGENTS.md, SOUL.md, ...)
```

Selma is installed in editable mode (`pip install -e .`), so `import selma` resolves to `src/selma/` from anywhere in the repo.

## 🚀 Installation

**Prerequisites:** Python 3.13+ and `venv` (both ship with a standard Python install).

```bash
git clone https://github.com/YOUR_USERNAME/agent-selma.git
cd agent-selma
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate.bat
pip install -e ".[dev]"
```

`pip install -e ".[dev]"` installs Selma itself (from `src/selma/`) in editable mode plus the dev dependencies (`pytest`, `ruff`, `pre-commit`), so `import selma` works everywhere and code changes take effect immediately without reinstalling.

Activate the git hooks once so lint/format checks run automatically on every commit:

```bash
pre-commit install
```

You can also run all checks manually at any time:

```bash
pre-commit run --all-files
```

**Ollama** (for local models) — download and install from [ollama.com](https://ollama.com/download), then pull a model:

```bash
ollama pull llama3.2        # recommended default (~2 GB)
ollama pull qwen2.5:14b     # more capable, requires ~9 GB RAM
```

Ollama runs as a background service automatically after installation. Verify it is running:

```bash
ollama list

curl http://localhost:11434/api/tags     # verfügbare Modelle (= ollama list)
curl http://localhost:11434/api/ps       # geladene Modelle (= ollama ps)

```

For the browser tool (Playwright), install Chromium once:

```bash
playwright install chromium
```

## ⚙️ Configuration

Run the setup script once to create the `.selma/` directory, generate a default `selma.json`, and deploy skills and templates into the workspace:

```bash
python -m selma.setup
```

The script is safe to re-run — it never overwrites existing files.

The generated `.selma/selma.json` contains all available options with their defaults. The most important setting is the model:

```json
"model": {
    "model": "ollama/llama3.1",
    ...
}
```

Use the format `"provider/model-name"`, e.g.:

- `"ollama/llama3.1"` — local Ollama instance (default)
- `"openai/gpt-4o"` — OpenAI API (requires `OPENAI_API_KEY` in `.env`)
- `"anthropic/claude-sonnet-4-6"` — Anthropic API (requires `ANTHROPIC_API_KEY` in `.env`)

For Ollama, the CPU thread count used for inference (`num_thread`) can only be set
reliably via a custom Modelfile tag — Selma talks to Ollama through the
OpenAI-compatible endpoint, which does not forward a per-request `num_thread`
option to the underlying runner. Create a tagged variant once:

```bash
cat <<'EOF' > Modelfile
FROM qwen3.6:27b
PARAMETER num_thread 16
EOF
ollama create qwen3.6-27b-t16 -f Modelfile
```

Then reference the tag in `selma.json`:

```json
"model": {
    "model": "ollama/qwen3.6-27b-t16"
}
```

If the Telegram channel is enabled, add the bot token to a `.env` file in the project root:

```env
TELEGRAM_TOKEN=...
```

The WebChat channel needs no additional credentials.

## ▶️ Running Selma

Start the gateway (FastAPI) and the dashboard (Streamlit) together:

```bash
./start.sh          # macOS / Linux
start.bat           # Windows
```

Or start them individually:

```bash
python -m selma.gateway                     # REST API on http://localhost:8000
streamlit run src/selma/dashboard.py
```

To restart only the gateway (e.g. after a code change):

```bash
./restart_gateway.sh    # macOS / Linux
restart_gateway.bat     # Windows
```

## 🖥 Dashboard

The **Streamlit dashboard** (`src/selma/dashboard.py`) is the primary web interface for chatting with Selma.

Start it together with the gateway via `./start.sh`, or standalone:

```bash
streamlit run src/selma/dashboard.py
```

The dashboard opens automatically in the browser at [http://localhost:8501](http://localhost:8501).

**Features:**

- **Streaming chat** — responses appear word-by-word as Selma generates them; active tool calls are shown inline while the agent works.
- **Session continuity** — each browser tab gets its own user ID, so multiple sessions run independently against the same gateway.
- **Settings dialog** — click ⚙️ in the sidebar to view or live-edit `.selma/selma.json` directly from the UI, with JSON validation before saving.

<!-- Add a screenshot here: images/dashboard_screenshot.png -->

## 🔌 Channels / Adapters

### WebChat

When `channels.webchat.enabled` is `true`, the gateway exposes a REST endpoint that any web client can use to chat with Selma.

### Telegram

Set `channels.telegram.enabled` to `true` and provide `TELEGRAM_TOKEN` in `.env`. The bot listens to direct messages and group mentions (`@BotName`).

## 🧩 Skills

Skills extend Selma's behaviour without touching core code. Each skill lives in its own folder:

```txt
skills/
  <skill-name>/
    SKILL.md        ← definition (YAML frontmatter + Markdown instructions)
```

Minimal `SKILL.md`:

```markdown
---
name: my-skill
description: "One sentence describing when to trigger this skill."
user-invocable: true
---

# My Skill

## When to use
...

## Steps
1. ...
```

Built-in skills: `summarize`, `web-research`, `blogwatcher`, `healthcheck`.

## 🔭 Tracing & Monitoring

Selma uses [OpenTelemetry](https://opentelemetry.io/) to emit spans for every agent run and records all LLM calls via the [OpenInference](https://github.com/Arize-ai/openinference) instrumentation. The traces are collected and visualised by [Arize Phoenix](https://phoenix.arize.com/) — an open-source LLM observability tool that runs entirely locally.

**Installation** — Phoenix is already listed as a dependency and is installed automatically by `uv sync`. No separate account or cloud service is needed.

**Starting the collector:**

Phoenix is started automatically by `start.sh` / `start.bat` together with the gateway and dashboard. The UI is available at [http://localhost:6006](http://localhost:6006), logs go to `phoenix.log`.

To start Phoenix standalone (e.g. for debugging without the full stack):

```bash
./start_otel.sh     # macOS / Linux
start_otel.bat      # Windows
```

In the Phoenix UI you can inspect:

- every agent run as a trace with individual spans
- LLM input/output messages and token counts
- tool calls and their results
- Python log records attached to their span

Tracing is **opt-in**: if Phoenix is not running, Selma operates normally with a no-op tracer — nothing breaks.

## 🧪 Test Scripts

Scripts marked **pytest** are discovered and run by `pytest`. The others are standalone scripts that must be run directly — they require a running Ollama instance and a configured `.selma/selma.json`.

| Script | How to run | What it tests |
|---|---|---|
| `test_unit_heartbeat.py` | pytest | Heartbeat scheduling unit tests |
| `test_unit_memory.py` | pytest | Memory index and search unit tests |
| `test_agent.py` | direct | Basic agent call without session persistence |
| `test_agent_session.py` | direct | AgentSession event subscription (streaming) |
| `test_agent_session_chat.py` | direct | Interactive CLI chat with spinner, `/info`, `/reset_session` |
| `test_agent_session_continue.py` | direct | Resuming an existing session |
| `test_bootstrap_chat.py` | direct | Bootstrap flow (first-run system prompt) |
| `test_function_call.py` | direct | Tool / function-call round-trip for all Ollama models |
| `test_runtime.py` | direct | Full agent runtime integration |
| `test_skills.py` | direct | Skills snapshot loading and hashing |
| `test_webchat.py` | direct | WebChat streaming end-to-end (direct function call) |
| `test_webchat_http.py` | direct | WebChat streaming end-to-end via HTTP gateway |

Run the pytest suite:

```bash
pytest
```

Run a standalone script:

```bash
python tests/test_agent.py
```

## 🎮 Related Projects

### Agent Selma Game

[**agentselma-game**](https://github.com/gkvoelkl/agentselma-game) — *Play an Agent in an OpenClaw-like Architecture.*

A browser-based game (self-contained HTML5, no build step) inspired by the C64 classic *Elevator Action*: you play Agent Selma, collect context documents from behind locked doors across eight floors, query a chat model and deliver the answers to the drones on the roof. Along the way the mechanics teach the same concepts this repository implements — context window management, debugging, channel routing and prompt-injection defense.

## 📅 Status

**Version 1.0** — feature-complete first release (June 2026).

## ⚠️ Disclaimer

> [!IMPORTANT]
> **No Contributions Yet:** At this stage, I am not accepting Pull Requests or changes. I am focusing on the initial build to establish the core learning path.

**Communication:**
Feel free to reach out with questions or thoughts! However, please understand that due to time constraints, I may not be able to respond to every message personally.
