# test for agent.py 

import asyncio
import json
import logging
from pathlib import Path
from colorama import Fore, Style

import colorama
from my_mono.pydantic_agent import Agent, AgentOptions, AgentTool, ToolSchema, UserMessage

colorama.init()

# ─── LOGGING ────────────────────────────────────────────────

# Set all other modules (openai, httpx, ...) to WARNING
logging.basicConfig(level=logging.WARNING)

# Set only the agent logger to DEBUG
logging.getLogger("my_mono.agent").setLevel(logging.DEBUG)
logging.getLogger("my_mono.agent").addHandler(
    logging.StreamHandler()
)
logging.getLogger("my_mono.agent").propagate = False
# propagate=False prevents events from also being
# forwarded to the root logger

# custom format for the agent logger only
handler = logging.getLogger("my_mono.agent").handlers[0]
handler.setFormatter(logging.Formatter(
    fmt="%(asctime)s %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
))

# ─── TOOLS ──────────────────────────────────────────────────

def read_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")

read_tool = AgentTool(
    name="read_file",
    description="Reads a file from disk and returns its content.",
    parameters=ToolSchema(
        properties={
            "path": {
                "type": "string",
                "description": "Path to the file to read",
            }
        },
        required=["path"],
    ),
    execute=read_file,
)

# ─── AGENT SETUP ────────────────────────────────────────────

agent_1 = Agent(
    AgentOptions(
        model="llama3.2",
        system_prompt="You are a helpful assistant."
    )
)

agent_2 = Agent(
    AgentOptions(
        model="llama3.2",
        system_prompt="You are a helpful assistant.",
        tools=[read_tool]
    )
)

# ─── EVENT LISTENER ─────────────────────────────────────────

def on_event(event):
    match event.type:
        case "message_end":
            print(Fore.BLUE + event.payload.content + Style.RESET_ALL, end="", flush=True)
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
    print("Test 2: Read config.json")
    print("=" * 50)
    Path("config.json").write_text(
        json.dumps({"version": "1.0", "debug": True}, indent=2)
    )
    await agent_2.prompt(UserMessage(content="Read config.json and summarize its content."))


if __name__ == "__main__":
    asyncio.run(main())