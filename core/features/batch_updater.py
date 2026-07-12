"""
BatchFeatureUpdater — планировщик batch-обновления признаков.

Проблема:
  FeatureEngine обновляет признаки поканально (один символ за раз).
  Каждое событие шины триггерит compute() для одного символа,
  что приводит к N×M запросам при масштабировании.

Решение (Unit-of-Work):
  1. Собирать символы, требующие обновления, в `_pending` очередь.
  2. Фоновый цикл каждые N секунд запускает batch-обновление для всех
     накопившихся символов.
  3. FeatureEngine.refresh_symbols(symbols) вызывает compute_batch()
     для каждого калькулятора и сохраняет результаты атомарно.
  4. get_feature() по умолчанию не блокирует — возвращает stale-данные
     и планирует фоновый refresh (stale-while-revalidate).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.features.engine import FeatureEngine

logger = logging.getLogger(__name__)

# Типы приоритетов для планирования
PRIORITY_EVENT = 0   # событие с шины — наивысший приоритет
PRIORITY_STALE = 1   # протухший TTL (get_feature со stale data)
PRIORITY_TTL = 2     # плановое обновление по TTL


class BatchFeatureUpdater:
    """Планировщик batch-обновления признаков.

    Usage:
        updater = BatchFeatureUpdater(feature_engine, interval=5.0)
        await updater.start()
        # ...
        await updater.schedule("BTC/USDT:USDT")  # событие
        # ...
        await updater.stop()
    """

    def __init__(
        self,
        feature_engine: FeatureEngine,
        interval: float = 5.0,
    ):
        self._fe = feature_engine
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._pending: set[str] = set()
        self._lock = asyncio.Lock()
        self._running = False

    # ── Lifecycle ──

    async def start(self):
        """Запустить фоновый цикл batch-обновления."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("[batch] BatchFeatureUpdater started (interval=%.1fs)", self.interval)

    async def stop(self):
        """Остановить фоновый цикл и обработать оставшиеся символы."""
        was_running = self._running
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Финальный flush (даже если start() не вызывался)
        async with self._lock:
            remaining = list(self._pending)
            self._pending.clear()
        if remaining:
            await self._flush(remaining)
        if was_running:
            logger.info("[batch] BatchFeatureUpdater stopped (flushed %d)", len(remaining))

    # ── API ──

    async def schedule(self, symbol: str, priority: int = PRIORITY_EVENT):
        """Добавить символ в очередь на обновление.

        Args:
            symbol: тикер
            priority: 0=event, 1=stale, 2=ttl (не используется,
                      но может быть расширением для сортировки)
        """
        async with self._lock:
            self._pending.add(symbol)

    async def schedule_many(self, symbols: list[str]):
        """Добавить несколько символов в очередь."""
        async with self._lock:
            self._pending.update(symbols)

    async def force_refresh(self, symbols: list[str] | None = None):
        """Принудительное обновление (без ожидания интервала).

        Args:
            symbols: None = обработать все pending
        """
        async with self._lock:
            to_process = list(self._pending) if symbols is None else symbols
            if symbols is not None:
                self._pending.difference_update(symbols)
            else:
                self._pending.clear()
        if to_process:
            await self._flush(to_process)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    # ── Internal ──

    async def _loop(self):
        """Фоновый цикл: каждые interval секунд запускает flush."""
        while self._running:
            try:
                await asyncio.sleep(self.interval)
                async with self._lock:
                    symbols = list(self._pending)
                    self._pending.clear()
                if symbols:
                    await self._flush(symbols)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[batch] loop error")

    async def _flush(self, symbols: list[str]):
        """Запустить batch-обновление для списка символов.

        Делегирует FeatureEngine.refresh_symbols(symbols),
        который в свою очередь вызывает compute_batch() для каждого
        калькулятора.
        """
        try:
            await self._fe.refresh_symbols(symbols)
            logger.debug("[batch] flushed %d symbols", len(symbols))
        except Exception:
            logger.exception("[batch] flush error for %d symbols", len(symbols))


# ── Singleton ──

_batch_updater: BatchFeatureUpdater | None = None


def get_batch_updater(feature_engine=None, interval: float = 5.0) -> BatchFeatureUpdater:
    global _batch_updater
    if _batch_updater is None and feature_engine is not None:
        _batch_updater = BatchFeatureUpdater(feature_engine, interval=interval)
    return _batch_updater


def reset_batch_updater():
    global _batch_updater
    _batch_updater = None
