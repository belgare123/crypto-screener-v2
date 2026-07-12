"""
OrderBook Feature Calculator — стакан, спред, дисбаланс, стены, айсберги.
Подписывается на orderbook.* и вычисляет производные признаки.

Производит фичи:
- ob.best_bid: float
- ob.best_ask: float
- ob.spread: float
- ob.imbalance: float (bid_volume / ask_volume)
- ob.bid_volume: float
- ob.ask_volume: float
- ob.walls: dict — стены (объём, цена)
- ob.iceberg: bool — подозрение на айсберги
"""

from __future__ import annotations

import logging
import time
from collections import deque

from core import Event
from core.features.base import BaseFeatureCalculator

logger = logging.getLogger(__name__)


class OrderBookFeatureCalculator(BaseFeatureCalculator):
    """
    Калькулятор orderbook-признаков.
    """

    event_channels = ["orderbook.*"]
    feature_names = [
        "ob.best_bid",
        "ob.best_ask",
        "ob.spread",
        "ob.spread_pct",
        "ob.imbalance",
        "ob.bid_volume",
        "ob.ask_volume",
        "ob.walls",
        "ob.iceberg",
    ]
    default_ttl = 1.0  # стакан обновляется часто — TTL 1 сек
    priority = 15

    WALL_THRESHOLD_RATIO = 5.0
    MAX_LEVELS = 20

    def __init__(self, feature_store=None):
        super().__init__(feature_store)
        # Последнее состояние стакана: symbol -> dict
        self._states: dict[str, dict] = {}
        # История спреда для тренда
        self._spread_history: dict[str, deque] = {}

    async def compute(self, symbol: str, event: Event | None = None) -> dict:
        """Вычислить OB-фичи."""
        state = self._states.get(symbol)
        if state is None:
            return {}

        bids = state.get("bids", [])
        asks = state.get("asks", [])

        if not bids or not asks:
            return {}

        best_bid = bids[0][0]
        best_ask = asks[0][0]
        spread = best_ask - best_bid
        spread_pct = spread / best_bid if best_bid > 0 else 0.0
        bid_vol = sum(b[1] for b in bids)
        ask_vol = sum(a[1] for a in asks)
        imbalance = (bid_vol / ask_vol) if ask_vol > 0 else float("inf")

        walls = self._detect_walls(bids, asks)
        iceberg = self._detect_iceberg(bids, asks)

        return {
            "ob.best_bid": best_bid,
            "ob.best_ask": best_ask,
            "ob.spread": spread,
            "ob.spread_pct": round(spread_pct, 6),
            "ob.imbalance": round(imbalance, 4),
            "ob.bid_volume": bid_vol,
            "ob.ask_volume": ask_vol,
            "ob.walls": walls,
            "ob.iceberg": iceberg,
        }

    async def on_event(self, event: Event):
        """Обновить состояние стакана."""
        data = event.data
        bids_raw = data.get("b", []) or data.get("bids", [])
        asks_raw = data.get("a", []) or data.get("asks", [])

        if not bids_raw and not asks_raw:
            return

        bids = [[float(p), float(s)] for p, s in bids_raw]
        asks = [[float(p), float(s)] for p, s in asks_raw]
        # Сортируем
        bids = sorted(bids, key=lambda x: -x[0])[:self.MAX_LEVELS]
        asks = sorted(asks, key=lambda x: x[0])[:self.MAX_LEVELS]

        self._states[event.symbol] = {
            "bids": bids,
            "asks": asks,
            "updated_at": time.time(),
        }

        # Тренд спреда
        if bids and asks:
            h = self._spread_history.setdefault(event.symbol, deque(maxlen=100))
            h.append(asks[0][0] - bids[0][0])

        await super().on_event(event)

    def _detect_walls(self, bids: list, asks: list) -> dict:
        """Поиск стен в стакане."""
        bid_walls = []
        for i in range(1, len(bids) - 1):
            size = bids[i][1]
            avg_neighbors = (bids[i - 1][1] + bids[i + 1][1]) / 2
            if avg_neighbors > 0 and size / avg_neighbors >= self.WALL_THRESHOLD_RATIO:
                bid_walls.append({
                    "price": bids[i][0],
                    "size": size,
                    "ratio": round(size / avg_neighbors, 1),
                })

        ask_walls = []
        for i in range(1, len(asks) - 1):
            size = asks[i][1]
            avg_neighbors = (asks[i - 1][1] + asks[i + 1][1]) / 2
            if avg_neighbors > 0 and size / avg_neighbors >= self.WALL_THRESHOLD_RATIO:
                ask_walls.append({
                    "price": asks[i][0],
                    "size": size,
                    "ratio": round(size / avg_neighbors, 1),
                })

        return {
            "bid_walls": bid_walls,
            "ask_walls": ask_walls,
            "bid_count": len(bid_walls),
            "ask_count": len(ask_walls),
        }

    def _detect_iceberg(self, bids: list, asks: list) -> bool:
        """Эвристика на айсберги: повторяющиеся объёмы на соседних уровнях."""
        for levels in [bids, asks]:
            sizes = [s for _, s in levels[:10]]
            if len(sizes) >= 3:
                for i in range(len(sizes) - 2):
                    chunk = sizes[i:i + 3]
                    total = sum(chunk)
                    if total > 0 and (max(chunk) - min(chunk)) / total < 0.1:
                        return True
        return False
