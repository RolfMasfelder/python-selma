import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from telegram import Update
from telegram.ext import ContextTypes

from selma.adapter_telegram import TelegramChannel
from selma.adapter_webchat import WebChatChannel
from selma.command_manager import CommandManager

# load_config() reads from .selma/selma.json — single source of truth,
from selma.config import load_config, resolve_timeout
from selma.data import NormalizedTurnInput, WebChatIn
from selma.runtime import DeliveryContext, RuntimeEnv
from selma.runtime import agent_command as run_agent
from selma.tracing import setup as tracing_setup
from selma.tracing import tracer

# -- Initialize core components (before api so lifespan can reference them)
config = load_config()
_command_manager = CommandManager(config)

# Slash commands that bypass the CommandManager and are forwarded to the
# agent as plain text so the matching skill can handle them.
_AGENT_PASSTHROUGH_COMMANDS: set[str] = {"/healthcheck"}

# -- Heartbeat state
_pending_alerts: asyncio.Queue[str] = asyncio.Queue()

# -- Background task registry: asyncio only holds a weak reference to a task,
# so a fire-and-forget task with no other referent can be garbage-collected
# mid-run. Keep a strong reference here for the task's lifetime, and drain
# the set on shutdown so no task is silently left running or abandoned.
_background_tasks: set[asyncio.Task] = set()


def _on_background_task_done(task: asyncio.Task) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # The task's own coroutine (e.g. _run() below) already catches and
        # reports its expected errors; this is only a safety net for anything
        # that still escapes it, so the same error may be logged twice.
        logging.error("Background task failed", exc_info=exc)


def _spawn_background_task(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_on_background_task_done)
    return task


@asynccontextmanager
async def lifespan(app: FastAPI):
    from selma.heartbeat import heartbeat_loop

    hb_task = asyncio.create_task(heartbeat_loop(config, ".", _pending_alerts))
    try:
        yield
    finally:
        hb_task.cancel()
        # Awaiting a cancelled task always raises CancelledError; let it
        # propagate so real cancellation of the current task isn't masked.
        await hb_task

        # Drain any still-running background tasks (e.g. WebChat stream
        # handlers) instead of leaving them to be torn down mid-run.
        pending = list(_background_tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


api = FastAPI(title="Selma Agent Gateway", lifespan=lifespan)


def _resolve_passthrough(body: str) -> str | None:
    """
    Returns the agent-facing message for passthrough commands, or None.

    /skill <name> [input]  → "Run the skill <name> [input]"
    /healthcheck           → "healthcheck"       (strips leading slash)
    anything else          → None                (not a passthrough)
    """
    parts = body.split(None, 2)
    cmd = parts[0].lower()

    if cmd == "/skill":
        if len(parts) < 2:
            return None
        name = parts[1]
        extra = f" {parts[2]}" if len(parts) == 3 else ""
        return f"Run the skill {name}{extra}"

    if cmd in _AGENT_PASSTHROUGH_COMMANDS:
        return body.lstrip("/")

    return None


async def _dispatch_command(ctx: NormalizedTurnInput, delivery: DeliveryContext | None = None) -> str | None:
    """
    Command dispatch.
    Returns a reply string (command result) or None to run the agent.
    """
    if ctx.body_for_commands and ctx.body_for_commands.startswith("/"):
        agent_text = _resolve_passthrough(ctx.body_for_commands)
        if agent_text is not None:
            ctx.body_for_agent = agent_text
        else:
            return await _command_manager.handle(ctx, delivery)

    return None


async def process_message_flow(ctx: NormalizedTurnInput, delivery: DeliveryContext) -> str | None:
    """
    Unified message flow for all channels.
    Returns a reply string for command results,
    or None when the agent handled the response via delivery callbacks.
    """
    ctx.pretty_print(ctx)
    reply = await _dispatch_command(ctx, delivery)
    if reply is not None:
        return reply

    try:
        await run_agent(
            ctx.body_for_agent or "",
            session_key=ctx.session_key,
            delivery=delivery,
            runtime=RuntimeEnv(cwd="."),
        )
        return None
    except TimeoutError:
        return "This is taking a bit longer — please try again."
    except Exception as e:
        return f"Error: {e}"


# -- SSE helper
def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


# -- WebChat streaming generator
async def process_message_flow_stream(ctx: NormalizedTurnInput):
    queue: asyncio.Queue[str | dict | None] = asyncio.Queue()
    delivery = WebChatChannel.deliver(queue)

    async def _run() -> None:
        try:
            reply = await process_message_flow(ctx, delivery)
            if reply:
                await queue.put({"type": "chunk", "text": reply})
        except Exception as e:
            # Wrapped as a dict (not a bare string) so real streamed model text
            # can never be mistaken for this internal error signal.
            await queue.put({"type": "error", "message": str(e)})
        finally:
            await queue.put(None)

    _spawn_background_task(_run())

    # Idle timeout per item — resets after each received event.
    # This matches the httpx read-timeout semantics on the client side.
    idle_timeout = max(resolve_timeout(config), 300)
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=idle_timeout)
        except TimeoutError:
            yield _sse({"type": "error", "message": "Stream timeout."})
            break
        if item is None:
            break
        if isinstance(item, dict):
            yield _sse(item)
            if item.get("type") == "error":
                break
            continue
        yield _sse({"type": "chunk", "text": item})

    yield _sse({"type": "done", "session_key": ctx.session_key})


# -- channel WebChat
@api.post("/webchat/stream")
@tracer.agent(name="handle_webchat")
async def handle_webchat(incoming: WebChatIn):
    try:
        ctx: NormalizedTurnInput = WebChatChannel.normalize(incoming.model_dump())
        return StreamingResponse(
            process_message_flow_stream(ctx),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except Exception as e:
        logging.exception("WebChat stream error")
        raise HTTPException(status_code=500, detail=str(e)) from e


# -- Heartbeat poll endpoint
@api.get("/webchat/heartbeat/poll")
async def poll_heartbeat_alert():
    """
    Returns the oldest pending heartbeat alert and removes it from the queue.
    Returns {"alert": null} when no alert is pending.
    The WebChat frontend polls this endpoint periodically.
    """
    try:
        alert = _pending_alerts.get_nowait()
        return {"alert": alert}
    except asyncio.QueueEmpty:
        return {"alert": None}


# -- channel Telegram
@tracer.agent(name="handle_telegram")
async def handle_telegram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ctx: NormalizedTurnInput = TelegramChannel.normalize(update)
        delivery = TelegramChannel.deliver(update)
        reply = await process_message_flow(ctx, delivery)
        if reply:  # command or error — not handled by delivery callbacks
            await update.message.reply_text(reply)
    except Exception:
        logging.exception("Telegram error")


_registry: list = [TelegramChannel(), WebChatChannel()]


# -- Main program & event loop
async def run_gateway():
    tracing_setup(endpoint="http://localhost:4317")

    # App-owned FileHandler: the process itself persists its log, so start.sh /
    # restart_gateway.sh no longer need to redirect stdout/stderr to a file.
    # Handlers are added directly (not via basicConfig): tracing_setup() already
    # attached an OTel handler to the root logger, and basicConfig() is a no-op
    # once handlers exist — using force=True there would silently drop the OTel
    # handler again and break Phoenix log export.
    log_file = Path("gateway.log")
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(console_handler)
    root.addHandler(file_handler)
    logging.info("Gateway logging to %s", log_file.resolve())

    tasks = [ch.start(config) for ch in _registry if ch.is_enabled(config)]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    try:
        asyncio.run(run_gateway())
    except KeyboardInterrupt:
        print("\n Gateway shutting down...")
