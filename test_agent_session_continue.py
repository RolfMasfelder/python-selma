from my_mono.tracing import setup; setup()

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from my_mono.agent import UserMessage
from my_mono.agent_session import create_agent_session, CreateSessionOptions, SessionManager
from my_mono.test_helper import setup_logger
from my_mono.tools import create_read_only_tools
from my_mono.tracing import tracer

MODEL_NAME = "gemma4"

# ─── LOGGING ────────────────────────────────────────────────

logging.basicConfig(level=logging.WARNING)
setup_logger("my_mono.agent")
setup_logger("my_mono.agent_session")


# ─── MAIN ───────────────────────────────────────────────────

@tracer.agent(name="test_agent_session_continue")
async def main():
    print("\n" + "=" * 60)
    print("  🗂️  my_mono — Session Management")
    print("=" * 60 + "\n")

    cwd = Path.cwd()
    tools = create_read_only_tools(str(cwd))

    def on_event(event):
        if event.type == "message_update":
            print(event.payload, end="", flush=True)
        elif event.type == "agent_end":
            print()


    # ── 1. New persistent session (creates .jsonl file) ──────
    print(f"\n── 1️⃣  New persistent session (creates .jsonl file) {'─' * 8}")
    new_session = await create_agent_session(CreateSessionOptions(
        model=MODEL_NAME,
        tools=tools,
        session_manager=SessionManager.create(cwd),
    ))
    print(f"  session_file: {new_session.session_file}")

    new_session.subscribe(on_event)
    await new_session.prompt("Hello, my name is George.")
    print()

    # ── 2. Resume most recent session (or create new) ────────
    print(f"\n── 2️⃣  Resume most recent session (continueRecent) {'─' * 8}")
    continued = await create_agent_session(CreateSessionOptions(
        model=MODEL_NAME,
        tools=tools,
        session_manager=SessionManager.continue_recent(cwd),
    ))
    print(f"  session_file: {continued.session_file}")

    continued.subscribe(on_event)
    await continued.prompt("What is my name?")
    print()

    print("✅ Done\n")


if __name__ == "__main__":
    asyncio.run(main())