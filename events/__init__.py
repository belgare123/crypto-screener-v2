"""
Event Engine — типобезопасная система событий рынка.

Строится поверх MarketDataBus (V1), добавляя:
1. MarketEvent — базовый класс с типизированными данными
2. EventBus — типобезопасный роутер + фильтры
3. Domain events: CandleEvent, WhaleEvent, VolumeEvent
"""

from __future__ import annotations

from .base import MarketEvent, EventBus, get_event_bus
from .candles import CandleEvent
from .whale import WhaleEvent
from .volume import VolumeEvent

__all__ = [
    "MarketEvent",
    "EventBus",
    "get_event_bus",
    "CandleEvent",
    "WhaleEvent",
    "VolumeEvent",
]
