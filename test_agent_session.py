# ============================================================
# test_session.py
# ============================================================

import asyncio
import logging
from pathlib import Path

from my_mono.pydantic_agent import AgentTool, ToolSchema
from my_mono.agent_session import create_agent_session, CreateSessionOptions

# ─── LOGGING ────────────────────────────────────────────────

logging.basicConfig(level=logging.WARNING)

for name in ("my_mono.agent", "my_mono.agent_session"):
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    ))
    log = logging.getLogger(name)
    log.setLevel(logging.DEBUG)
    log.addHandler(handler)
    log.propagate = False


# ─── TOOLS ──────────────────────────────────────────────────

def list_directory(path: str = ".") -> str:
    """Returns all files and folders in the given directory."""
    entries = sorted(Path(path).iterdir())
    lines = []
    for entry in entries:
        kind = "dir " if entry.is_dir() else "file"
        lines.append(f"{kind}  {entry.name}")
    return "\n".join(lines) if lines else "(empty)"


list_dir_tool = AgentTool(
    name="list_directory",
    description="Lists all files and folders in a directory.",
    parameters=ToolSchema(          
        properties={
            "path": {
                "type": "string",
                "description": "Directory path to list.",
            }
        },
        required=[],
    ),
    execute=list_directory,
)


# ─── MAIN ───────────────────────────────────────────────────

async def main():
    session = await create_agent_session(CreateSessionOptions(
        model="llama3.1:8b",
        tools=[list_dir_tool],
    ))

    # Print streaming text live — equivalent to process.stdout.write(delta)
    def on_event(event):
        if event.type == "message_update":
            print(event.payload, end="", flush=True)
        elif event.type == "agent_end":
            print()

    session.subscribe(on_event)

    await session.prompt("What files are in the current directory?")

    # Print all messages — equivalent to session.state.messages.forEach(...)
    print()
    for msg in session.state.messages:
        print(msg.model_dump())
    print()


if __name__ == "__main__":
    asyncio.run(main())