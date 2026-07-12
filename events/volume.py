"""
VolumeEvent — типизированные события объёма.

Фиксирует:
- Аномальные всплески объёма
- Снижение/затухание
- Соотношение buy/sell объёма
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from events.base import MarketEvent, register_event


@register_event
@dataclass
class VolumeEvent(MarketEvent):
    """Событие изменения объёма / аномалии."""

    # Данные
    timeframe: str = ""         # 5m, 15m, 1h, 24h
    current_volume: float = 0.0
    avg_volume: float = 0.0     # средний объём за период
    volume_ratio: float = 1.0   # current / avg (1.0 = норма)

    buy_volume: float = 0.0
    sell_volume: float = 0.0

    is_surge: bool = False      # аномальный всплеск
    is_drop: bool = False       # аномальное падение

    surge_multiplier: float = 0.0  # во сколько раз выше нормы

    @property
    def channel_key(self) -> str:
        return f"volume.{self.timeframe}"

    @property
    def buy_ratio(self) -> float:
        """Доля покупок в общем объёме (0-1)."""
        total = self.buy_volume + self.sell_volume
        if total == 0:
            return 0.5
        return self.buy_volume / total

    @property
    def sell_ratio(self) -> float:
        return 1.0 - self.buy_ratio
