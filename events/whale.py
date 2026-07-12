"""
WhaleEvent — типизированные события китовых сделок.

Фиксирует крупные сделки, кластеры и аномалии.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from events.base import MarketEvent, register_event


@register_event
@dataclass
class WhaleEvent(MarketEvent):
    """Событие крупной сделки / кластера китовых ордеров."""

    # Данные сделки
    side: str = ""           # buy / sell
    price: float = 0.0
    size: float = 0.0        # размер сделки в контрактах
    value_usdt: float = 0.0  # стоимость сделки в USDT

    # Доп. метрики
    is_cluster: bool = False        # кластер мелких ордеров
    cluster_avg_price: float = 0.0  # средняя цена кластера
    cluster_total_value: float = 0.0

    # Контекст
    timestamp_ms: float = 0.0       # unix ms
    exchange_trade_id: str = ""

    @property
    def channel_key(self) -> str:
        return "whale.trade"

    @classmethod
    def from_data(
        cls,
        symbol: str,
        exchange: str,
        side: str,
        price: float,
        size: float,
        value_usdt: float,
        timestamp_ms: float,
        **kwargs,
    ) -> WhaleEvent:
        """Удобный factory method."""
        return cls(
            symbol=symbol,
            exchange=exchange,
            ts=timestamp_ms,
            side=side,
            price=price,
            size=size,
            value_usdt=value_usdt,
            timestamp_ms=timestamp_ms,
            **kwargs,
        )
