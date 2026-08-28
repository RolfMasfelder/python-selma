# ============================================================
# task_manager unit tests
#
# Deckung für selma/task_manager.py (spawn, _on_done, shutdown).
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import asyncio

from selma import task_manager


def test_spawn_returns_tracked_task_that_completes():
    async def work():
        return 42

    async def run():
        task = task_manager.spawn(work(), name="tm.test")
        assert isinstance(task, asyncio.Task)
        result = await task
        assert result == 42
        # nach Abschluß nicht mehr im Registry-Set
        return task

    task = asyncio.new_event_loop().run_until_complete(run())
    assert task not in task_manager._tasks


def test_on_done_removes_task_and_ignores_cancelled():
    async def slow():
        await asyncio.sleep(30)

    async def run() -> asyncio.Task:
        task = task_manager.spawn(slow(), name="tm.cancel")
        task.cancel()
        await asyncio.sleep(0)  # laß den Cancel laufen
        return task

    task = asyncio.new_event_loop().run_until_complete(run())
    assert task.cancelled()

    before = set(task_manager._tasks)
    task_manager._tasks.add(task)
    task_manager._on_done(task)
    assert task not in task_manager._tasks
    assert task_manager._tasks == before


def test_on_done_logs_exception_for_failed_task():
    async def bad():
        raise ValueError("kaputt")

    async def run():
        task = task_manager.spawn(bad(), name="tm.bad")
        try:
            await task
        except Exception:
            pass
        return task

    task = asyncio.new_event_loop().run_until_complete(run())
    assert task not in task_manager._tasks
    assert task.exception() is not None


def test_shutdown_cancels_pending_and_is_idempotent():
    async def slow():
        await asyncio.sleep(30)

    async def run():
        t1 = task_manager.spawn(slow(), name="tm.slow.1")
        t2 = task_manager.spawn(slow(), name="tm.slow.2")
        await task_manager.shutdown()
        assert t1.cancelled() and t2.cancelled()

        # zweite Schließung: nichts offen, darf nicht hängen bleiben
        await task_manager.shutdown()

        # spawn danach geht weiterhin
        async def ok():
            return 1

        t3 = task_manager.spawn(ok())
        return await t3

    assert asyncio.new_event_loop().run_until_complete(run()) == 1
    assert task_manager._tasks == set()
