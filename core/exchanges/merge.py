"""
MultiExchangeMerge — консолидация потоков с нескольких бирж по символу.

Даёт:
- Единый MarketDataStream для каждого символа (Bybit + Binance + OKX + Deribit)
- Консенсус цены: медиана, средневзвешенная по объёму, VWAP
- Unified OB: не делаем (слишком тяжёлый), но считаем спред-консенсус
"""
from __future__ import annotations

import logging
import statistics
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ConsolidatedPrice:
    """Консенсусная цена для символа на основе данных с нескольких бирж."""
    symbol: str
    median_price: float = 0.0
    vwap_price: float = 0.0       # средневзвешенная по объёму
    spread_bps: float = 0.0        # средний спред в bps
    last_prices: dict[str, float] = field(default_factory=dict)  # exchange → price
    participation: dict[str, float] = field(default_factory=dict)  # exchange → % volume
    total_volume_24h: float = 0.0
    ts: float = 0.0


class MultiExchangeMerge:
    """Объединяет нормализованные данные с нескольких бирж.

    Состояние:
    - _prices[symbol][exchange] = float (последняя цена)
    - _volumes[symbol][exchange] = float (24h объём)
    """

    def __init__(self):
        self._prices: dict[str, dict[str, float]] = defaultdict(dict)
        self._volumes: dict[str, dict[str, float]] = defaultdict(dict)

    def update_trade(self, trade: TradeData):
        """Обновить последнюю цену и объём для символа/биржи."""
        self._prices[trade.symbol][trade.exchange] = trade.price
        self._volumes[trade.symbol][trade.exchange] = self._volumes[trade.symbol].get(
            trade.exchange, 0
        ) + trade.value_usdt

    def update_ticker(self, symbol: str, exchange: str, price: float, volume_24h: float = 0):
        self._prices[symbol][exchange] = price
        if volume_24h > 0:
            self._volumes[symbol][exchange] = volume_24h

    def consolidate(self, symbol: str) -> ConsolidatedPrice | None:
        """Вычислить консенсус по символу."""
        prices = self._prices.get(symbol, {})
        if not prices:
            return None

        vol = self._volumes.get(symbol, {})
        total_vol = sum(vol.values()) or 1.0

        # Медианная цена
        price_list = list(prices.values())
        median = statistics.median(price_list) if len(price_list) > 1 else price_list[0]

        # VWAP (средневзвешенная по объёму)
        vwap = sum(p * vol.get(e, 0) for e, p in prices.items()) / total_vol

        # Участие бирж в %
        participation = {e: v / total_vol * 100 for e, v in vol.items()} if total_vol > 1 else {}

        # Спред — не вычисляем без OB, ставим 0
        cp = ConsolidatedPrice(
            symbol=symbol,
            median_price=median,
            vwap_price=round(vwap, 2),
            last_prices=dict(prices),
            participation=participation,
            total_volume_24h=sum(vol.values()),
            ts=time.time() * 1000,
        )
        logger.debug(
            "[merge] %s median=%.2f vwap=%.2f exchanges=%d vol=%.0f",
            symbol, median, vwap, len(prices), cp.total_volume_24h,
        )
        return cp

    def get_all_exchanges(self, symbol: str) -> list[str]:
        return list(self._prices.get(symbol, {}).keys())

    def clear(self, symbol: str | None = None):
        if symbol:
            self._prices.pop(symbol, None)
            self._volumes.pop(symbol, None)
        else:
            self._prices.clear()
            self._volumes.clear()


# Fix: forward reference for TradeData
from core.exchanges.base import TradeData  # noqa: E402, F811
