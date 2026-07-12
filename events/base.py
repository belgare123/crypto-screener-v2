"""
MarketEvent & EventBus — типобезопасная событийная система.

- MarketEvent: базовый класс для всех доменных событий
- EventBus: строит типизированный слой поверх MarketDataBus

Usage:
    bus = get_event_bus()
    bus.subscribe(CandleEvent, handler, symbols=["BTC/USDT:USDT"])
    ...

    event = CandleEvent(
        symbol="BTC/USDT:USDT", exchange="bybit",
        timeframe="1m", open=100.0, high=105.0,
        low=99.0, close=104.0, volume=1234.5,
    )
    await bus.publish(event)
"""

from __future__ import annotations

import abc
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, TypeVar

from core import Event as BusEvent, MarketDataBus, get_bus as _get_bus

logger = logging.getLogger(__name__)


# ── Type vars ──

E = TypeVar("E", bound="MarketEvent")
EventHandler = Callable[[E], Coroutine[Any, Any, None]]


# ── Базовые типы событий ──

@dataclass
class MarketEvent(abc.ABC):
    """Базовый класс для всех доменных событий рынка.

    Каждый подкласс обязан:
    - Определить ``channel_key`` (строка для MarketDataBus)
    - Указать типизированные поля
    """

    symbol: str
    exchange: str
    ts: float = field(default_factory=lambda: time.time() * 1000)

    @property
    @abc.abstractmethod
    def channel_key(self) -> str:
        """Ключ для роутинга через EventBus (e.g. 'candle.1m')."""
        ...

    def to_bus_event(self) -> BusEvent:
        """Конвертировать MarketEvent → core.Event для публикации."""
        return BusEvent(
            channel=f"{self.channel_key}.{self.symbol}",
            exchange=self.exchange,
            symbol=self.symbol,
            data=self._to_dict(),
            ts=self.ts,
        )

    def _to_dict(self) -> dict[str, Any]:
        """Сериализация в dict (кроме symbol/exchange/ts)."""
        return {
            k: v for k, v in self.__dict__.items()
            if k not in ("symbol", "exchange", "ts")
        }

    @classmethod
    def from_dict(cls: type[E], symbol: str, exchange: str, data: dict, ts: float) -> E:
        """Восстановить из dict (для replay)."""
        return cls(symbol=symbol, exchange=exchange, ts=ts, **data)


# ── Типобезопасный EventBus ──

HandlerWithFilter = tuple[EventHandler, list[str] | None]  # (handler, [symbols] or None)


class EventBus:
    """Типобезопасная обёртка над MarketDataBus.

    Позволяет:
    - Подписываться на тип события (CandleEvent, WhaleEvent…)
    - Фильтровать по символам
    - Получать типизированный event в handler
    """

    def __init__(self, bus: MarketDataBus | None = None):
        self._bus = bus or _get_bus()
        self._handlers: dict[type[MarketEvent], list[HandlerWithFilter]] = {}
        # Привязка к MarketDataBus — слушаем все события
        self._bus.subscribe("*", self._route)

    async def _route(self, ev: BusEvent):
        """Внутренний роутер: BusEvent → typed MarketEvent → dispatch."""
        # Пропускаем, если это не typed event (нет channel_key)
        if not ev.data or "_event_type" not in ev.data:
            return

        event_type_name = ev.data.pop("_event_type", None)
        event_cls = _TYPE_REGISTRY.get(event_type_name)
        if event_cls is None:
            return

        try:
            typed_event = event_cls.from_dict(
                symbol=ev.symbol,
                exchange=ev.exchange,
                data=ev.data,
                ts=ev.ts,
            )
        except Exception:
            logger.exception("[eventbus] Failed to reconstruct %s", event_type_name)
            return

        # Dispatch
        handlers = self._handlers.get(event_cls, [])
        for handler, symbols_filter in handlers:
            if symbols_filter is not None and typed_event.symbol not in symbols_filter:
                continue
            try:
                await handler(typed_event)
            except Exception:
                logger.exception(
                    "[eventbus] Handler %s crashed on %s %s",
                    handler.__name__, event_type_name, typed_event.symbol,
                )

    def subscribe(
        self,
        event_cls: type[MarketEvent],
        handler: EventHandler,
        symbols: list[str] | None = None,
    ):
        """Подписаться на события типа event_cls.

        Args:
            event_cls: класс события (CandleEvent, WhaleEvent…)
            handler: coroutine handler
            symbols: список символов, или None для всех
        """
        self._handlers.setdefault(event_cls, []).append((handler, symbols))
        logger.info(
            "[eventbus] subscribed %s → %s (symbols=%s)",
            handler.__name__, event_cls.__name__,
            symbols or "ALL",
        )

    def unsubscribe(self, event_cls: type[MarketEvent], handler: EventHandler):
        handlers = self._handlers.get(event_cls, [])
        self._handlers[event_cls] = [
            (h, f) for h, f in handlers if h is not handler
        ]

    async def publish(self, event: MarketEvent):
        """Опубликовать MarketEvent — пройдёт через MarketDataBus."""
        bus_ev = event.to_bus_event()
        # Добавляем метку типа для _route
        type_name = type(event).__name__
        bus_ev.data["_event_type"] = type_name
        await self._bus.publish(bus_ev)


# ── Registry (для десериализации) ──

_TYPE_REGISTRY: dict[str, type[MarketEvent]] = {}


def register_event(cls: type[MarketEvent]) -> type[MarketEvent]:
    """Декоратор для авто-регистрации типа."""
    _TYPE_REGISTRY[cls.__name__] = cls
    return cls


# ── Singleton ──

_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def reset_event_bus():
    global _event_bus
    _event_bus = None
