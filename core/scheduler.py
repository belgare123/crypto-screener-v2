"""Scheduler — периодические задачи (HeatMap, health-check, cleanup)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class ScheduledTask:
    interval: float          # секунды
    callback: Callable[[], Awaitable]
    name: str = ""
    run_immediately: bool = False
    _task: asyncio.Task | None = None

class Scheduler:
    """
    Планировщик повторяющихся задач.
    - HeatMap (30s)
    - Health-check WebSocket (60s)
    - Cleanup старых данных (1h)
    """

    def __init__(self):
        self._tasks: list[ScheduledTask] = []
        self._running = False

    def add(self, interval: float, callback: Callable[[], Awaitable], name: str = "", run_immediately: bool = False):
        self._tasks.append(ScheduledTask(
            interval=interval,
            callback=callback,
            name=name or callback.__name__,
            run_immediately=run_immediately,
        ))

    async def start(self):
        self._running = True
        for t in self._tasks:
            if t.run_immediately:
                asyncio.create_task(self._run_and_schedule(t))
            else:
                t._task = asyncio.create_task(self._loop(t))
        logger.info("Scheduler started with %d tasks", len(self._tasks))

    async def stop(self):
        self._running = False
        for t in self._tasks:
            if t._task and not t._task.done():
                t._task.cancel()
        await asyncio.gather(*[t._task for t in self._tasks if t._task], return_exceptions=True)

    async def _loop(self, task: ScheduledTask):
        while self._running:
            try:
                await task.callback()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Scheduled task '%s' failed", task.name)
            await asyncio.sleep(task.interval)

    async def _run_and_schedule(self, task: ScheduledTask):
        try:
            await task.callback()
        except Exception:
            logger.exception("Scheduled task '%s' failed on first run", task.name)
        task._task = asyncio.create_task(self._loop(task))
