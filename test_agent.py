# test for agent.py 

import asyncio
import logging
import colorama
from colorama import Fore, Style
from my_mono.agent import Agent, AgentOptions, UserMessage
from my_mono.tools import create_coding_tools
from my_mono.test_helper import setup_logger

colorama.init()

# ─── SETUP ────────────────────────────────────────────

logging.basicConfig(level=logging.WARNING)
setup_logger("my_mono.agent")

tools = create_coding_tools(cwd=".")

MODEL_NAME = "gemma4"

agent_1 = Agent(AgentOptions(
    model=MODEL_NAME,
    system_prompt="You are a helpful assistant.",
))

agent_2 = Agent(AgentOptions(
    model=MODEL_NAME,
    system_prompt="You are a helpful assistant.",
    tools=tools,
))


# ─── EVENT LISTENER ─────────────────────────────────────────

def on_event(event):
    match event.type:
        case "message_end":
            print(Fore.BLUE + (event.payload.content or "") + Style.RESET_ALL,
                  end="", flush=True)
        case "agent_end":
            print()


agent_1.subscribe(on_event)
agent_2.subscribe(on_event)


# ─── TESTS ──────────────────────────────────────────────────

async def main():
    print("=" * 50)
    print("Test 1: Hello")
    print("=" * 50)
    await agent_1.prompt(UserMessage(content="Hello!"))

    print("=" * 50)
    print("Test 2: List and read a file")
    print("=" * 50)
    await agent_2.prompt(UserMessage(content="List the current directory and read the README.md if it exists."))


if __name__ == "__main__":
    asyncio.run(main())