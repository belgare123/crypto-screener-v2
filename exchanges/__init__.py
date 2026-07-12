"""Базовый класс для всех биржевых адаптеров."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any

from core import Event, MarketDataBus, get_bus

logger = logging.getLogger(__name__)


class ExchangeBase(ABC):
    """Абстракция биржи. Управляет WebSocket-подписками."""

    name: str = ""  # binance | bybit | okx

    def __init__(self, bus: MarketDataBus | None = None):
        self.bus = bus or get_bus()
        self._ws: Any = None          # WebSocket connection
        self._running = False
        self._subscriptions: set[str] = set()

    @abstractmethod
    async def connect(self):
        """Открыть WebSocket."""

    @abstractmethod
    async def disconnect(self):
        """Закрыть WebSocket."""

    @abstractmethod
    async def subscribe(self, channel: str, symbols: list[str]):
        """Подписаться на канал (candles, trades, orderbook и т.д.)."""

    @abstractmethod
    async def unsubscribe(self, channel: str, symbols: list[str]):
        """Отписаться от канала."""

    @abstractmethod
    async def _listen(self):
        """Основной цикл получения сообщений из WebSocket."""

    async def start(self):
        self._running = True
        await self.connect()
        asyncio.create_task(self._listen_loop())

    async def stop(self):
        self._running = False
        await self.disconnect()

    async def _listen_loop(self):
        while self._running:
            try:
                await self._listen()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("%s: listen loop crashed, reconnecting in 5s", self.name)
                await asyncio.sleep(5)

    def _emit(self, channel: str, symbol: str, data: dict, ts: float | None = None):
        """Опубликовать событие в шину."""
        import time
        event = Event(
            channel=channel,
            exchange=self.name,
            symbol=symbol,
            data=data,
            ts=ts or time.time() * 1000,
            received_at=time.time(),
        )
        asyncio.ensure_future(self.bus.publish(event))
