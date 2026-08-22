# ============================================================
# test_agent_session_chat.py
#
# Simple interactive chat client using AgentSession.
# Continues the most recent session on startup.
#
# Commands:
#   /bye           — exit the program
#   /reset_session — discard current session, start a new one
#   /info          — print system prompt + full message history
# ============================================================

from selma.tracing import setup

setup()

import argparse
import asyncio
import itertools
import os
import sys

from colorama import Fore, Style
from colorama import init as colorama_init

from selma.agent_session import AgentSession, CreateSessionOptions, SessionManager, create_agent_session
from selma.my_tools import create_coding_tools, create_read_only_tools
from selma.tracing import tracer

colorama_init(autoreset=False)

MODEL_NAME = "qwen3.6:27b"

# ─── LOGGING ────────────────────────────────────────────────

# logging.basicConfig(level=logging.WARNING)
# setup_logger("selma.agent")
# setup_logger("selma.agent_session")

# ─── HELPERS ────────────────────────────────────────────────

RESET = Style.RESET_ALL
BOLD = Style.BRIGHT
DIM = Style.DIM
CYAN = Fore.CYAN
YELLOW = Fore.YELLOW
GREEN = Fore.GREEN
RED = Fore.RED


def print_separator(char: str = "─", width: int = 60) -> None:
    print(DIM + char * width + RESET)


def print_command_hint() -> None:
    print(DIM + "  /bye  /reset_session  /info" + RESET)
    print()


# ─── SPINNER ────────────────────────────────────────────────


class Spinner:
    """
    Async spinner shown on its own indented line below "Assistant:".
    Cleared before response text is printed — no overwrite possible.
    """

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    INTERVAL = 0.08

    def __init__(self, label: str = "thinking"):
        self._label = label
        self._task: asyncio.Task | None = None
        self._stopped = False

    def start(self) -> None:
        self._stopped = False
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        print("\r\033[2K", end="", flush=True)

    async def _run(self) -> None:
        for frame in itertools.cycle(self.FRAMES):
            if self._stopped:
                break
            print(f"\r  {DIM}{frame} {self._label}…{RESET}", end="", flush=True)
            await asyncio.sleep(self.INTERVAL)


# ─── SESSION FACTORY ────────────────────────────────────────


async def make_session(continue_recent: bool = True, tools=None) -> AgentSession:
    cwd = os.getcwd()
    session_manager = SessionManager.continue_recent(cwd=cwd) if continue_recent else SessionManager.create(cwd=cwd)
    return await create_agent_session(
        CreateSessionOptions(
            model=MODEL_NAME,
            session_manager=session_manager,
            tools=tools or [],
        )
    )


# ─── COMMANDS ───────────────────────────────────────────────


def cmd_info(session: AgentSession) -> None:
    print()
    print_separator("═")
    print(BOLD + "  SYSTEM PROMPT" + RESET)
    print_separator()
    print(session.agent._options.system_prompt)

    print()
    print_separator("═")
    print(BOLD + "  MESSAGE HISTORY  " + DIM + f"({len(session.state.messages)} messages)" + RESET)
    print_separator()

    for i, msg in enumerate(session.state.messages, 1):
        color = CYAN if msg.role == "user" else GREEN if msg.role == "assistant" else YELLOW
        print(color + BOLD + f"[{i}] {msg.role.upper()}" + RESET)

        if msg.role == "assistant":
            if msg.content:
                print(msg.content)
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    print(DIM + f"  → tool_call: {tc.name}({tc.arguments})" + RESET)
        elif msg.role == "tool":
            print(DIM + f"  tool_call_id: {msg.tool_call_id}" + RESET)
            content = msg.content or ""
            print(content[:400] + DIM + " … [truncated]" + RESET if len(content) > 400 else content)
        else:
            print(msg.content or "")
        print()

    print_separator("═")
    print()


async def cmd_reset(current_session: AgentSession, tools=None) -> AgentSession:
    print(YELLOW + "  Starting new session…" + RESET)
    new_session = await make_session(continue_recent=False, tools=tools)
    print(GREEN + f"  New session: {new_session.session_file}" + RESET)
    print()
    return new_session


# ─── MAIN LOOP ───────────────────────────────────────────────


@tracer.agent
async def main() -> None:
    parser = argparse.ArgumentParser(description="Chat client")
    parser.add_argument(
        "--tools",
        choices=["coding", "readonly"],
        default="readonly",
        help="'coding' (read/bash/edit/write) or 'readonly' (read/grep/find/ls)",
    )
    args = parser.parse_args()

    cwd = os.getcwd()
    if args.tools == "readonly":
        tools = create_read_only_tools(cwd)
        tools_label = "read-only (read, grep, find, ls)"
    else:
        tools = create_coding_tools(cwd)
        tools_label = "coding (read, bash, edit, write)"

    print()
    print(BOLD + "  Chat Client" + RESET)
    print(DIM + f"  Tools: {tools_label}" + RESET)
    print_command_hint()

    session = await make_session(continue_recent=True, tools=tools)

    msg_count = len(session.state.messages)
    if msg_count > 0:
        print(DIM + f"  Resumed session: {session.session_file}  ({msg_count} messages)" + RESET)
    else:
        print(DIM + f"  New session: {session.session_file}" + RESET)
    print()

    spinner = Spinner()

    def make_listener():
        def on_event(event):
            if event.type == "message_update":
                if spinner._task and not spinner._stopped:
                    spinner.stop()
                delta: str = event.payload or ""
                print(delta, end="", flush=True)
            elif event.type == "tool_start":
                spinner.stop()
                print(f"  {YELLOW}⚙ {event.payload.name}{RESET}", flush=True)
            elif event.type == "tool_end":
                print(f"  {DIM}✓ {event.payload.name}{RESET}", flush=True)
                spinner.start()
            elif event.type == "agent_end":
                spinner.stop()
                print()

        return on_event

    def subscribe(sess: AgentSession) -> None:
        sess.subscribe(make_listener())

    subscribe(session)

    while True:
        try:
            print(CYAN + BOLD + "You: " + RESET, end="", flush=True)
            user_input = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
        except (EOFError, KeyboardInterrupt):
            print()
            break

        user_input = user_input.rstrip("\n")
        if not user_input.strip():
            continue

        if user_input.strip() == "/bye":
            print(DIM + "  Bye." + RESET)
            break

        if user_input.strip() == "/reset_session":
            session = await cmd_reset(session, tools=tools)
            spinner = Spinner()
            subscribe(session)
            continue

        if user_input.strip() == "/info":
            cmd_info(session)
            continue

        print(GREEN + BOLD + "Assistant:" + RESET, flush=True)
        spinner.start()
        try:
            await session.prompt(user_input)
        except Exception as e:
            spinner.stop()
            print(RED + f"  Error: {e}" + RESET)

        print()


if __name__ == "__main__":
    asyncio.run(main())
