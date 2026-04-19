import asyncio
import logging
from datetime import datetime
from pathlib import Path

from my_mono.agent import UserMessage
from my_mono.agent_session import create_agent_session, CreateSessionOptions, SessionManager
from my_mono.test_helper import setup_logger
from my_mono.tools import create_read_only_tools

MODEL_NAME = "gemma4"

# ─── LOGGING ────────────────────────────────────────────────

logging.basicConfig(level=logging.WARNING)
setup_logger("my_mono.agent")
setup_logger("my_mono.agent_session")


# ─── MAIN ───────────────────────────────────────────────────

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

    # ── 3. List all sessions ──────────────────────────────────
    print(f"\n── 3️⃣  List all existing sessions {'─' * 26}")
    session_dir = cwd / ".my_mono" / "sessions"
    all_files = sorted(
        session_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ) if session_dir.exists() else []

    print(f"  {len(all_files)} session(s) found:\n")
    for path in all_files:
        sm = SessionManager.open(path)
        meta = next((e for e in sm._entries if e.type == "session"), None)
        ts_fmt = (
            datetime.fromisoformat(meta.timestamp).astimezone().strftime("%m/%d/%Y, %H:%M:%S")
            if meta else "(unknown)"
        )
        first_user = next(
            (m.content for m in sm.build_context() if isinstance(m, UserMessage)),
            "(empty)",
        )
        print(f"  • {(sm.get_session_id() or '')[:8]}…  {ts_fmt}")
        print(f"    First message: \"{(first_user or '')[:50]}…\"")
        print(f"    File: {path}\n")

    # ── 4. Open a specific session ────────────────────────────
    if all_files:
        print(f"\n── 4️⃣  Open a specific session (oldest) {'─' * 21}")
        oldest = all_files[-1]
        print(f"  Opening: {oldest}")

        opened = await create_agent_session(CreateSessionOptions(
            model=MODEL_NAME,
            tools=tools,
            session_manager=SessionManager.open(oldest),
        ))
        print(f"  session_id:   {opened.session_id}")
        print(f"  session_file: {opened.session_file}")

        opened.subscribe(on_event)
        await opened.prompt("What was our first conversation about?")
        print()

    print("✅ Done\n")


if __name__ == "__main__":
    asyncio.run(main())