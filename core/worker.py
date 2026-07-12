"""Worker pool — управляет корутинами-воркерами для параллельной обработки монет."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Callable

logger = logging.getLogger(__name__)

class WorkerPool:
    """
    Пул воркеров для параллельной обработки сигналов по монетам.
    Вместо одного цикла по 500 монет — N воркеров берут задачи из очереди.
    """

    def __init__(self, num_workers: int = 10, queue_size: int = 1000):
        self.num_workers = num_workers
        self.queue: asyncio.Queue[tuple[str, Callable]] = asyncio.Queue(maxsize=queue_size)
        self._workers: list[asyncio.Task] = []
        self._running = False

    async def start(self):
        self._running = True
        for i in range(self.num_workers):
            task = asyncio.create_task(self._worker_loop(i), name=f"worker-{i}")
            self._workers.append(task)
        logger.info("WorkerPool started with %d workers", self.num_workers)

    async def stop(self):
        self._running = False
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def submit(self, symbol: str, task: Callable[[], Awaitable]):
        """Добавить задачу в очередь."""
        await self.queue.put((symbol, task))

    async def _worker_loop(self, idx: int):
        while self._running:
            try:
                symbol, task = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                try:
                    await task()
                except Exception:
                    logger.exception("[worker-%d] Error processing %s", idx, symbol)
                finally:
                    self.queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
