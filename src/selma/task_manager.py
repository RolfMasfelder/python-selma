# ============================================================
# task_manager.py
#
# Central registry for fire-and-forget asyncio tasks.
#
# Non-invasive by design: callers still decide when to spawn a task via
# spawn(); this module only prevents premature GC of untracked tasks and
# guarantees they are cancelled/awaited once at process shutdown.
#
# Not for tasks that already manage their own lifecycle end-to-end
# (e.g. gateway.py's heartbeat task, AgentSession.prompt()) — those keep
# using asyncio.create_task()/await directly, unchanged.
# ============================================================

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

_tasks: set[asyncio.Task[Any]] = set()


def spawn[T](coro: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task[T]:
    """
    Fire-and-forget a coroutine as a tracked background task.

    Keeps a strong reference so the task can't be garbage-collected mid-run,
    and logs any exception not already handled by the coroutine itself.
    """
    task = asyncio.create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_on_done)
    return task


def _on_done(task: asyncio.Task[Any]) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # The task's own coroutine may already catch and report its expected
        # errors; this is only a safety net for anything that still escapes
        # it, so the same error may be logged twice.
        logger.error("Background task %s failed", task.get_name(), exc_info=exc)


async def shutdown() -> None:
    """
    Cancels and awaits all still-running tracked tasks.

    Call once at process shutdown so nothing is left running or abandoned.
    Safe to call multiple times (e.g. once per channel's own shutdown path):
    a second call simply finds nothing pending.
    """
    pending = list(_tasks)
    if not pending:
        return
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
