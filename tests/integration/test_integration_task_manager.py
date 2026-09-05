# ============================================================
# task_manager INTEGRATION tests
#
# Im Gegensatz zu tests/unit/test_unit_task_manager.py (klasseneigene
# Logik, isoliert) laufen hier echte Hintergrund-Tasks durch den
# kompletten spawn() → Laufzeit → shutdown()-Zyklus — inklusive
# Real-World-Nutzung (Events in eine Queue produceren, wie
# gateway.py es macht) und Edge-Cases:
#   - Task, der bei Loop-Wechsel überlebt (früher: "different loop"
#     in shutdown() → RuntimeError / ValueError)
#   - shutdown() auf einer frischen Loop mit Fremdtasks aus
#     geschlossenen Loops → muss reclaimen, nicht crashen
#   - Exception in echem Task → _on_done-Log + Registry sauber
#
# Run via: bash tests/scripts/run_tests.sh
# ============================================================

import asyncio
import json
from pathlib import Path

import pytest

from selma import task_manager


def run(loop: asyncio.AbstractEventLoop, coro):
    """Führt `coro` auf der Loop aus und gibt das Ergebnis zurück."""
    return loop.run_until_complete(coro)


class TestRealTaskLifecycle:
    """Echte Tasks (Queue-Produzent, Dateischreiber) durch spawn/shutdown."""

    def test_spawned_producer_delivers_all_events_before_shutdown(self, tmp_path):
        async def producer(queue: asyncio.Queue):
            for i in range(5):
                await asyncio.sleep(0)  # echte Yield-Punkte, nicht sofort fertig
                await queue.put({"seq": i, "payload": f"msg-{i}"})

        async def scenario():
            queue: asyncio.Queue = asyncio.Queue()
            task = task_manager.spawn(producer(queue), name="tm.int.producer")
            received = [await queue.get() for _ in range(5)]
            result = await task  # producer gibt None zurück → sauber beendet
            assert result is None
            assert [e["seq"] for e in received] == [0, 1, 2, 3, 4]
            await task_manager.shutdown()
            return received

        received = run(asyncio.new_event_loop(), scenario())
        assert len(received) == 5

    def test_shutdown_cancels_real_slow_task_and_nothing_outlives_it(self, tmp_path):
        side_effects = tmp_path / "ticks.jsonl"

        async def slow_writer(path: Path):
            # Simuliert eine endlose Hintergrundarbeit (wie heartbeat-Loops)
            i = 0
            while True:
                await asyncio.sleep(0)
                with path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"tick": i}) + "\n")
                i += 1

        async def scenario():
            t = task_manager.spawn(slow_writer(side_effects), name="tm.int.slow")
            await asyncio.sleep(0)  # Task mindestens eine Umdrehung laufen lassen
            await asyncio.sleep(0)
            line_count = sum(1 for _ in side_effects.open(encoding="utf-8"))
            await task_manager.shutdown()
            assert t.cancelled()
            # Nach dem Cancel keine neuen Ticks mehr (mind. 1 Umdrehung lassen)
            await asyncio.sleep(0)
            final = sum(1 for _ in side_effects.open(encoding="utf-8"))
            # shutdown() hat awaited → keine Races: Datei-Größe stabil
            assert final == line_count or final == line_count + 1
            return t

        t = run(asyncio.new_event_loop(), scenario())
        assert t.cancelled()
        assert task_manager._tasks == set()

    def test_failed_real_task_is_logged_and_registry_stays_clean(self, tmp_path, caplog):
        import logging

        async def boom():
            await asyncio.sleep(0)
            raise ValueError("produktionsrelevanter Fehler")

        async def scenario():
            t = task_manager.spawn(boom(), name="tm.int.boom")
            with pytest.raises(ValueError):
                await t
            assert t not in task_manager._tasks
            await task_manager.shutdown()  # darf sauber durchlaufen

        with caplog.at_level(logging.ERROR, logger="selma.task_manager"):
            run(asyncio.new_event_loop(), scenario())
            # _on_done hat die Exception protokolliert (Safety-Net)
        assert any("tm.int.boom" in r.message or "failed" in r.message.lower() for r in caplog.records)


class TestCrossLoopEdgeCases:
    """
    Die Ursprungs-Bugklasse: Tasks aus einer geschlossenen Loop landen sonst
    im globalen _tasks-Set; der nächste shutdown()/Task-Wechsel auf einer
    frischen Loop crasht dann mit
        RuntimeError: ... got Future ... attached to a different loop
    oder ValueError: Future belongs to a different loop.

    task_manager.shutdown() muss Fremdtasks jetzt reclaimen (skip + discard),
    statt zu crashen. In Produktion (eine Loop) passiert das nie.
    """

    def _leave_orphan_task(self) -> asyncio.Task:
        """Loop A: Task spawnen (läuft weiter), Loop A schließen OHNE shutdown."""
        loop = asyncio.new_event_loop()

        async def hang():
            await asyncio.sleep(3600)  # lebt ewig weiter

        async def setup():
            # spawn() braucht eine laufende Loop → innerlich, nicht außen
            return task_manager.spawn(hang(), name="tm.int.orphan")

        task = loop.run_until_complete(setup())
        loop.run_until_complete(asyncio.sleep(0))  # Task mindestens einmal starten
        loop.close()  # Loop weg, Task bleibt im globalen Set → Orphan
        return task

    def test_shutdown_on_fresh_loop_reclaims_orphans_instead_of_crashing(self):
        orphan = self._leave_orphan_task()
        assert orphan in task_manager._tasks  # verschmutzter Zustand, wie vor dem Fix

        async def scenario():
            await task_manager.shutdown()  # früher: ValueError/RunTimeError (different loop)
            # Orphan ist nicht mehr im Set, aber NICHT von uns 'bearbeitet'
            assert orphan not in task_manager._tasks

            # Neuster Task auf dieser Loop läuft trotzdem normal:
            async def ok():
                return 7

            return await task_manager.spawn(ok(), name="tm.int.after")

        assert run(asyncio.new_event_loop(), scenario()) == 7

    def test_multiple_loops_in_sequence_do_not_contaminate(self):
        """
        Drei hintereinanderliegende Loops, jeder lässt einen hängenden Task
        zurück — der alte Volllauf-Case: einmal rot, danach alles rot.
        Jetzt: jeder Loop für sich, der finale shutdown() reclaimt alle
        Orphans, ohne auf fremden Loops aufzutraben.
        """
        orphans = []
        for i in range(3):
            loop = asyncio.new_event_loop()

            # B023: i per Default-Argument in den Closure-Scope binden,
            # damit beide Tasks den Wert dieses Loop-Durchlaufs sehen.
            async def scenario(ix=i):
                async def hang():
                    await asyncio.sleep(3600)

                alive = task_manager.spawn(hang(), name=f"tm.int.loop{ix}.hang")

                async def quick():
                    return ix

                result = await task_manager.spawn(quick(), name=f"tm.int.loop{ix}.quick")
                return alive, result  # Orphan bewusst zurücklassen (kein shutdown)

            alive, quick_result = loop.run_until_complete(scenario())
            loop.close()
            orphans.append(alive)
            assert quick_result == i

        # Verschmutzter Zustand wie vor dem Fix: 3 Fremdtasks, 3 geschlossene Loops
        assert set(task_manager._tasks) == set(orphans)

        async def final_loop():
            await task_manager.shutdown()  # früher: ValueError/RunTimeError (different loop)
            return len(task_manager._tasks)

        assert run(asyncio.new_event_loop(), final_loop()) == 0

    def test_shutdown_is_idempotent_with_orphans_present(self):
        """Doppeltes shutdown() in Anwesenheit von Orphans: beide Male grün."""
        self._leave_orphan_task()

        async def scenario():
            await task_manager.shutdown()
            await task_manager.shutdown()
            await task_manager.shutdown()

        for _ in range(3):  # jeder Call auf einer frischen Loop
            run(asyncio.new_event_loop(), scenario())


class TestGatewayShapeUsage:
    """
    Real-World-Nutzung aus gateway.py nachstellen:
    process_message_flow_stream spawn()t _run() und der Consumer bricht
    irgendwann ab (idle-timeout) — der Producer Task bleibt 'hängen',
    shutdown() muss ihn zuverlässig cancel()en, ohne den Consumer zu
    stören.
    """

    def test_abandoned_stream_producer_is_cleaned_up_by_shutdown(self):
        async def _run(queue: asyncio.Queue):
            # simulierter Agent-Stream: viele Events, dann Pause
            for i in range(50):
                await queue.put({"type": "chunk", "text": f"t{i}"})
                await asyncio.sleep(0)
            await asyncio.sleep(3600)  # 'hängt' wie ein echter Agent beim Streamen

        async def consumer():
            queue: asyncio.Queue = asyncio.Queue()
            task_manager.spawn(_run(queue), name="tm.webchat.run")
            got = 0
            while got < 5:  # Consumer bricht nach 5 Events ab (wie idle-timeout)
                await queue.get()
                got += 1
            await task_manager.shutdown()
            return got

        assert run(asyncio.new_event_loop(), consumer()) == 5
        assert task_manager._tasks == set()


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
