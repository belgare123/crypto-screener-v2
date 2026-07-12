"""
Core: базовые абстракции шины данных.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  Event — единица данных на шине
# ──────────────────────────────────────────────

@dataclass
class Event:
    """Одно событие от биржи: свеча, трейд, стакан, OI и т.д."""
    channel: str        # candles.BTCUSDT.5m | trades.BTCUSDT | orderbook.BTCUSDT
    exchange: str       # binance | bybit | okx
    symbol: str         # BTC/USDT:USDT
    data: dict          # тело события
    ts: float           # timestamp события (unix ms)
    received_at: float = 0.0  # когда получили

@dataclass
class SignalResult:
    """Результат проверки сигнала."""
    signal_name: str
    symbol: str
    exchange: str
    score: float        # 0-100
    direction: str      # buy | sell | neutral
    meta: dict          # детали (цена, объём и т.д.)
    ts: float           # timestamp
    cooldown: int = 1800  # антиспам cooldown в секундах

# ──────────────────────────────────────────────
#  MarketDataBus — asyncio-шина событий
# ──────────────────────────────────────────────

Handler = Callable[[Event], Coroutine[Any, Any, None]]

class MarketDataBus:
    """
    Publish-Subscribe шина.
    - Все компоненты публикуют Event сюда
    - Фильтры и подписчики получают события по подписке

    Позволяет:
    - Логировать весь поток для бэктеста (record=True)
    - Воспроизводить replay из лога
    - Делать много сигналов, не мешая друг другу
    """

    def __init__(self, record: bool = False):
        self._subscribers: dict[str, list[Handler]] = {}
        self._record = record
        self._recorded: list[Event] = []

    def subscribe(self, channel: str, handler: Handler):
        """Подписаться на канал. channel = 'candles.BTCUSDT.5m' или '*' (всё)."""
        self._subscribers.setdefault(channel, []).append(handler)
        logger.debug("Subscribed %s -> %s", handler.__name__, channel)

    def unsubscribe(self, channel: str, handler: Handler):
        handlers = self._subscribers.get(channel, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: Event):
        """Опубликовать событие — все подписчики получат его."""
        if self._record:
            self._recorded.append(event)

        # Точное совпадение
        for h in self._subscribers.get(event.channel, []):
            try:
                await h(event)
            except Exception:
                logger.exception("Handler %s crashed on %s", h.__name__, event.channel)

        # Wildcard: topics matching 'prefix.*'
        for pattern, handlers in list(self._subscribers.items()):
            if pattern.endswith(".*"):
                prefix = pattern[:-2]  # убираем .* в конце
                if event.channel == prefix or event.channel.startswith(prefix + "."):
                    for h in handlers:
                        try:
                            await h(event)
                        except Exception:
                            logger.exception("Handler %s crashed on %s", h.__name__, event.channel)

        # Глобальный wildcard '*'
        for h in self._subscribers.get("*", []):
            try:
                await h(event)
            except Exception:
                logger.exception("Handler %s crashed on %s", h.__name__, event.channel)

    @property
    def recorded(self) -> list[Event]:
        return self._recorded

    def replay(self, events: list[Event]):
        """Синхронно прогнать записанные события (для бэктеста)."""
        ...  # будет в backtest/replay.py


# ──────────────────────────────────────────────
#  Глобальная шина (singleton)
# ──────────────────────────────────────────────

_bus: MarketDataBus | None = None

def get_bus() -> MarketDataBus:
    global _bus
    if _bus is None:
        _bus = MarketDataBus(record=True)
    return _bus

def reset_bus():
    """Сброс (для тестов / replay)."""
    global _bus
    _bus = None
