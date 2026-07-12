"""Scanner modules — подписываются на Market Data Bus и обрабатывают сырые данные."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from core import Event, MarketDataBus, get_bus

logger = logging.getLogger(__name__)

class BaseScanner(ABC):
    """
    Базовый класс сканера.
    Сканер:
    1. Подписывается на канал в MarketDataBus
    2. Получает Event
    3. Агрегирует/буферизует данные
    4. Вызывает сигналы
    """

    name: str = "base"
    channel: str = "*"  # на какой канал подписываемся

    def __init__(self, bus: MarketDataBus | None = None):
        self.bus = bus or get_bus()
        self._running = False

    async def start(self):
        self._running = True
        self.bus.subscribe(self.channel, self._on_event)
        logger.info("Scanner '%s' started on channel '%s'", self.name, self.channel)

    async def stop(self):
        self._running = False
        self.bus.unsubscribe(self.channel, self._on_event)

    async def _on_event(self, event: Event):
        if not self._running:
            return
        try:
            await self.process(event)
        except Exception:
            logger.exception("Scanner '%s' error on %s", self.name, event.channel)

    @abstractmethod
    async def process(self, event: Event):
        """Обработать событие."""
