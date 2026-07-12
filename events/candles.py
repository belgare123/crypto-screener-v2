"""
CandleEvent — типизированные события свечей.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from events.base import MarketEvent, register_event


@register_event
@dataclass
class CandleEvent(MarketEvent):
    """Событие новой свечи с полным набором OHLCV."""

    timeframe: str = ""  # 1m, 5m, 15m, 1h …
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    turnover: float = 0.0
    complete: bool = False  # True, если свеча закрыта

    @property
    def channel_key(self) -> str:
        return f"candle.{self.timeframe}"

    @property
    def range_pct(self) -> float:
        """Процентный диапазон свечи."""
        if self.open == 0:
            return 0.0
        return (self.high - self.low) / self.open * 100

    @property
    def body_pct(self) -> float:
        """Процентная величина тела свечи."""
        if self.open == 0:
            return 0.0
        return abs(self.close - self.open) / self.open * 100

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_to_wick_ratio(self) -> float:
        """Отношение тела к общему диапазону (0-1)."""
        rng = self.high - self.low
        if rng == 0:
            return 1.0
        return abs(self.close - self.open) / rng
