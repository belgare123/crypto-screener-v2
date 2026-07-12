"""Dispatcher — принимает сигналы из SignalEngine и направляет в каналы оповещения."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from core import SignalResult
from utils import SignalCooldown

logger = logging.getLogger(__name__)

class Dispatcher:
    """
    Диспетчер сигналов.
    1. Берёт сигналы из SignalEngine
    2. Anti-spam (30 min cooldown + degrade)
    3. Priority filter (40-60 weak, 60-80 normal, 80-95 strong, 95-100 critical)
    4. Баффер: собирает до N сигналов за T секунд
    5. Отправляет в Telegram / WebSocket клиентам / Webhook
    6. Оповещает слушателей (SignalRecorder и др.)
    """

    def __init__(
        self,
        engine,
        notifier,
        min_score: float = 40.0,
        batch_interval: float = 2.0,
        max_batch: int = 10,
    ):
        self._engine = engine
        self._notifier = notifier
        self._min_score = min_score
        self._batch_interval = batch_interval
        self._max_batch = max_batch
        self._cooldown = SignalCooldown(default_cooldown=1800)
        self._running = False
        self._listeners: list[callable] = []  # вызываются для каждого отправленного сигнала
        self._tasks: list[asyncio.Task] = []

    def add_listener(self, fn: callable):
        """Подписать слушателя на каждый отправленный сигнал (асинхронный callable)."""
        self._listeners.append(fn)

    async def start(self):
        self._running = True
        self._tasks.append(asyncio.create_task(self._dispatch_loop()))
        logger.info("Dispatcher started (min_score=%.0f, batch=%ds)", self._min_score, self._batch_interval)

    async def stop(self):
        self._running = False

    async def _dispatch_loop(self):
        """Основной цикл: собирает сигналы, батчит, отправляет."""
        while self._running:
            batch: list[SignalResult] = []
            deadline = asyncio.get_event_loop().time() + self._batch_interval

            while asyncio.get_event_loop().time() < deadline:
                sig = await self._engine.get_signal()
                if sig is None:
                    await asyncio.sleep(0.1)
                    continue

                # Anti-spam: проверяем cooldown + degrade (не слать, если score не вырос)
                sig_cd = getattr(sig, "cooldown", 1800) or 1800
                if not self._cooldown.can_send(sig.signal_name, sig.symbol, sig.score, cooldown=sig_cd):
                    continue

                # Дедупликация внутри одного батча: если уже есть такой же сигнал
                # по той же монете — оставляем только с бóльшим score
                dup = False
                for existing in batch:
                    if existing.signal_name == sig.signal_name and existing.symbol == sig.symbol:
                        if sig.score > existing.score:
                            # заменяем на более сильный
                            batch.remove(existing)
                            batch.append(sig)
                        dup = True
                        break
                if dup:
                    continue

                batch.append(sig)
                if len(batch) >= self._max_batch:
                    break

            if batch and self._notifier:
                logger.info("[dispatch] sending %d signal(s): %s", len(batch), ", ".join(f"{s.signal_name}({s.score})" for s in batch))
                try:
                    if len(batch) == 1:
                        sig = batch[0]
                        await self._notifier.send_signal(sig)
                        self._notify_listeners(sig)
                    else:
                        await self._notifier.send_batch(batch)
                        for sig in batch:
                            self._notify_listeners(sig)
                except Exception:
                    logger.exception("Dispatcher: failed to send batch")

            await asyncio.sleep(0.1)

    def _notify_listeners(self, sig):
        """Оповестить слушателей (асинхронно, fire-and-forget)."""
        for fn in self._listeners:
            try:
                asyncio.ensure_future(fn(sig))
            except Exception:
                logger.exception("Dispatcher: listener %s crashed", fn)
