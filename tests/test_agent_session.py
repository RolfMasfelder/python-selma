from selma.my_mono.tracing import setup

setup()

import asyncio
import logging

from selma.my_mono.agent_session import CreateSessionOptions, create_agent_session
from selma.my_mono.test_helper import setup_logger
from selma.my_mono.tools import create_read_only_tools
from selma.my_mono.tracing import tracer

MODEL_NAME = "qwen2.5:7b"

# ─── LOGGING ────────────────────────────────────────────────

logging.basicConfig(level=logging.WARNING)
setup_logger("selma.my_mono.agent")
setup_logger("selma.my_mono.agent_session")


# ─── MAIN ───────────────────────────────────────────────────


@tracer.agent(name="test_agent_session")
async def main():
    session = await create_agent_session(
        CreateSessionOptions(
            model=MODEL_NAME,
            tools=create_read_only_tools(cwd="."),
        )
    )

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
