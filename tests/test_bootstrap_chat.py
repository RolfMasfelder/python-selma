# ============================================================
# test_bootstrap_chat.py
#
# Bootstrap chat: copies templates/*.md into the workspace,
# starts an interactive chat with Selma (bootstrap mode "full"),
# and shows changed workspace files at the end.
#
# Commands:
#   /bye   — exit chat and show changed files
#   /info  — show current bootstrap mode and session info
# ============================================================

from selma.my_mono.tracing import setup

setup()

import asyncio
import hashlib
import itertools
import shutil
import sys
from pathlib import Path

import phoenix as px
from colorama import Fore, Style
from colorama import init as colorama_init
from rich.console import Console
from rich.markdown import Markdown

from selma.my_mono.tracing import tracer
from selma.runtime import DeliveryContext, RuntimeEnv, agent_command

_console = Console()

colorama_init(autoreset=False)

RESET = Style.RESET_ALL
BOLD = Style.BRIGHT
DIM = Style.DIM
CYAN = Fore.CYAN
YELLOW = Fore.YELLOW
GREEN = Fore.GREEN
RED = Fore.RED
MAGENTA = Fore.MAGENTA

CWD = "."
WORKSPACE = Path(CWD) / ".selma" / "workspace"
TEMPLATES = Path(CWD) / "templates"
SESSIONS_DIR = Path(CWD) / ".selma" / "agents" / "main" / "sessions"
SESSION_KEY = "agent:main:main"


# ── Spinner ───────────────────────────────────────────────────────────────────


class Spinner:
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


# ── File snapshot ─────────────────────────────────────────────────────────────


def file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def snapshot(directory: Path) -> dict[str, str]:
    """Returns {relative_path: md5} for all files in directory."""
    result = {}
    if directory.exists():
        for f in sorted(directory.rglob("*")):
            if f.is_file():
                result[str(f.relative_to(directory))] = file_hash(f)
    return result


# ── Session reset ─────────────────────────────────────────────────────────────


def reset_bootstrap_session() -> None:
    """Deletes the sessions directory so every run starts with a clean slate."""
    if SESSIONS_DIR.exists():
        shutil.rmtree(SESSIONS_DIR)
        print(DIM + "  Previous session deleted." + RESET)


# ── Copy templates ────────────────────────────────────────────────────────────


def copy_templates() -> list[str]:
    """Copy templates/*.md → workspace. Returns list of copied filenames."""
    if not TEMPLATES.exists():
        print(RED + f"  No templates directory found: {TEMPLATES}" + RESET)
        return []

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in sorted(TEMPLATES.glob("*.md")):
        dst = WORKSPACE / src.name
        shutil.copy2(src, dst)
        copied.append(src.name)
    return copied


# ── Show diff ─────────────────────────────────────────────────────────────────


def print_diff(before: dict[str, str], after: dict[str, str]) -> None:
    all_keys = sorted(set(before) | set(after))
    changed = []
    for key in all_keys:
        b, a = before.get(key), after.get(key)
        if b is None:
            changed.append((GREEN + "  + added:    " + RESET, key))
        elif a is None:
            changed.append((RED + "  - deleted:  " + RESET, key))
        elif b != a:
            changed.append((YELLOW + "  ~ modified: " + RESET, key))

    print()
    print(BOLD + "  Changed workspace files:" + RESET)
    print(DIM + "  " + "─" * 40 + RESET)
    if changed:
        for label, name in changed:
            print(label + name)
    else:
        print(DIM + "  No changes." + RESET)
    print()


# ── Main ──────────────────────────────────────────────────────────────────────


@tracer.agent(name="test_bootstrap_chat")
async def main() -> None:
    px.launch_app()

    print()
    print(BOLD + "  Selma Bootstrap Chat" + RESET)
    print(DIM + "  " + "─" * 40 + RESET)

    # 1. Delete previous session to start fresh
    reset_bootstrap_session()

    # 2. Copy templates into workspace
    copied = copy_templates()
    if copied:
        print(GREEN + f"  Copied to {WORKSPACE}:" + RESET)
        for name in copied:
            marker = BOLD + " ★" + RESET if name == "BOOTSTRAP.md" else ""
            print(f"    {name}{marker}")
    print()

    # 3. Snapshot workspace before chat
    before = snapshot(WORKSPACE)

    runtime = RuntimeEnv(cwd=CWD)
    print(DIM + f"  Session key: {SESSION_KEY}" + RESET)
    print(DIM + "  /bye = exit   /info = session info" + RESET)
    print()

    # 4. Selma opens the conversation (bootstrap trigger)
    spinner = Spinner()

    async def send(message: str, show_you: bool = True) -> None:
        nonlocal spinner
        chunks: list[str] = []

        def on_partial(chunk: str) -> None:
            chunks.append(chunk)

        def on_tool_call(name: str, args: dict) -> None:
            spinner.stop()
            arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
            print(DIM + f"  ⚙ tool: {name}({arg_str})" + RESET, flush=True)
            spinner.start()

        def on_flush() -> None:
            pass

        delivery = DeliveryContext(
            on_partial_reply=on_partial,
            on_tool_call=on_tool_call,
            on_block_reply_flush=on_flush,
        )

        if show_you:
            print(CYAN + BOLD + "You: " + RESET + message)
        spinner.start()
        try:
            await agent_command(
                message,
                session_key=SESSION_KEY,
                runtime=runtime,
                delivery=delivery,
            )
        except Exception as e:
            spinner.stop()
            print(RED + f"  Error: {e}" + RESET)
            return
        finally:
            spinner.stop()

        text = "".join(chunks).strip()
        if text:
            print(GREEN + BOLD + "Selma:" + RESET)
            _console.print(Markdown(text))
        print()

    await send(".", show_you=False)

    # 5. Chat loop
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
            break

        if user_input.strip() == "/info":
            bootstrap_md = WORKSPACE / "BOOTSTRAP.md"
            status = GREEN + "present → mode=full" if bootstrap_md.exists() else DIM + "absent → mode=none"
            print(f"  BOOTSTRAP.md: {status}{RESET}")
            print(DIM + f"  Session key:  {SESSION_KEY}" + RESET)
            print()
            md_files = sorted(WORKSPACE.glob("*.md")) if WORKSPACE.exists() else []
            if md_files:
                for f in md_files:
                    print(BOLD + f"  ── {f.name} " + "─" * max(0, 38 - len(f.name)) + RESET)
                    _console.print(Markdown(f.read_text(encoding="utf-8")))
            else:
                print(DIM + "  Workspace: (empty)" + RESET)
            print()
            continue

        await send(user_input, show_you=False)

    # 5. Show changed workspace files
    after = snapshot(WORKSPACE)
    print_diff(before, after)


if __name__ == "__main__":
    asyncio.run(main())
