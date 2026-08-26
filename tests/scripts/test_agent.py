# test for agent.py

from selma.tracing import setup

setup()  # OTel aktivated

import asyncio
import logging

import colorama
from colorama import Fore, Style

from selma.agent import Agent, AgentOptions, UserMessage
from selma.my_tools import create_read_only_tools
from selma.test_helper import setup_logger
from selma.tracing import tracer

colorama.init()

# ─── SETUP ────────────────────────────────────────────

logging.basicConfig(level=logging.WARNING)
setup_logger("selma.agent")

MODEL_NAME = "qwen3.8-27b-t16"

tools = create_read_only_tools(cwd=".")

agent = Agent(
    AgentOptions(
        model=MODEL_NAME,
        system_prompt="You are a helpful assistant.",
        tools=tools,
    )
)


# ─── EVENT LISTENER ─────────────────────────────────────────


def on_event(event):
    match event.type:
        case "message_end":
            print(Fore.BLUE + (event.payload.content or "") + Style.RESET_ALL, end="", flush=True)
        case "agent_end":
            print()


agent.subscribe(on_event)


# ─── TESTS ──────────────────────────────────────────────────


@tracer.agent(name="test_agent")
async def main():
    print("=" * 50)
    print("Test: List and read a file")
    print("=" * 50)
    await agent.prompt(UserMessage(content="List the current directory and read the README.md if it exists."))


if __name__ == "__main__":
    asyncio.run(main())
