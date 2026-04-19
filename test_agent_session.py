import asyncio
import logging

from my_mono.agent_session import create_agent_session, CreateSessionOptions
from my_mono.test_helper import setup_logger
from my_mono.tools import create_read_only_tools

MODEL_NAME = "gemma4"

# ─── LOGGING ────────────────────────────────────────────────

logging.basicConfig(level=logging.WARNING)
setup_logger("my_mono.agent")
setup_logger("my_mono.agent_session")


# ─── MAIN ───────────────────────────────────────────────────

async def main():
    session = await create_agent_session(CreateSessionOptions(
        model=MODEL_NAME,
        tools=create_read_only_tools(cwd="."),
    ))

    def on_event(event):
        if event.type == "message_update":
            print(event.payload, end="", flush=True)
        elif event.type == "agent_end":
            print()

    session.subscribe(on_event)

    await session.prompt("What files are in the current directory?")

    print()
    for msg in session.state.messages:
        print(msg.model_dump())
    print()


if __name__ == "__main__":
    asyncio.run(main())