"""Order Book Scanner — анализ стакана, спуфинг, айсберги, дисбаланс."""

from __future__ import annotations

import time
from collections import defaultdict

from core import Event, get_bus
from scanner import BaseScanner


class OrderBookState:
    """
    Текущее состояние стакана для одного символа.
    Хранит bid/ask levels + метаданные.
    """

    def __init__(self, max_levels: int = 20):
        self.bids: list[list[float]] = []  # [[price, size], ...]
        self.asks: list[list[float]] = []
        self.max_levels = max_levels
        self.updated_at: float = 0.0

    def update(self, bids: list[list[float]], asks: list[list[float]]):
        self.bids = sorted(bids, key=lambda x: -x[0])[:self.max_levels]
        self.asks = sorted(asks, key=lambda x: x[0])[:self.max_levels]
        self.updated_at = time.time()

    @property
    def bid_volume(self) -> float:
        return sum(b[1] for b in self.bids)

    @property
    def ask_volume(self) -> float:
        return sum(a[1] for a in self.asks)

    @property
    def imbalance_ratio(self) -> float:
        if self.ask_volume == 0:
            return float("inf")
        return self.bid_volume / self.ask_volume

    @property
    def best_bid(self) -> float:
        return self.bids[0][0] if self.bids else 0.0

    @property
    def best_ask(self) -> float:
        return self.asks[0][0] if self.asks else 0.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid

    def detect_walls(self, threshold_ratio: float = 5.0) -> dict:
        """
        Ищет стенки: уровень, где объём в N+ раз больше соседних.
        Возвращает:
        {
            "bid_walls": [(price, size, ratio), ...],
            "ask_walls": [(price, size, ratio), ...],
            "imbalance": 8.1
        }
        """
        bid_walls = []
        for i, (price, size) in enumerate(self.bids):
            if i > 0 and i < len(self.bids) - 1:
                avg_neighbors = (self.bids[i-1][1] + self.bids[i+1][1]) / 2
                if avg_neighbors > 0 and size / avg_neighbors >= threshold_ratio:
                    bid_walls.append((price, size, round(size / avg_neighbors, 1)))

        ask_walls = []
        for i, (price, size) in enumerate(self.asks):
            if i > 0 and i < len(self.asks) - 1:
                avg_neighbors = (self.asks[i-1][1] + self.asks[i+1][1]) / 2
                if avg_neighbors > 0 and size / avg_neighbors >= threshold_ratio:
                    ask_walls.append((price, size, round(size / avg_neighbors, 1)))

        return {
            "bid_walls": bid_walls,
            "ask_walls": ask_walls,
            "imbalance": round(self.imbalance_ratio, 2),
        }

    def detect_iceberg(self, levels_similar: int = 3) -> bool:
        """
        Простая эвристика на айсберги:
        несколько одинаковых объёмов на соседних уровнях (бот скрывает заявку).
        """
        for levels in [self.bids, self.asks]:
            sizes = [s for _, s in levels[:10]]
            if len(sizes) >= levels_similar:
                for i in range(len(sizes) - levels_similar + 1):
                    chunk = sizes[i:i + levels_similar]
                    if sum(chunk) > 10 and max(chunk) > 0 and (max(chunk) - min(chunk)) / max(chunk) < 0.1:
                        return True
        return False

    def detect_spoof(self) -> bool:
        """
        Эвристика спуфинга:
        крупный лимитник далеко от цены, который исчезает за одно обновление.
        """
        # TODO: трекинг изменений между снапшотами
        return False


# глобальное состояние стаканов
orderbooks: dict[str, OrderBookState] = defaultdict(lambda: OrderBookState(max_levels=20))


class OrderBookScanner(BaseScanner):
    """Слушает orderbook.* и обновляет OrderBookState."""

    name = "orderbook"
    channel = "orderbook.*"

    async def process(self, event: Event):
        data = event.data
        ob = orderbooks[event.symbol]

        # Bybit: {"s":"BTCUSDT","b":[["price","size"]],"a":[...]}
        bids = [[float(p), float(s)] for p, s in data.get("b", [])]
        asks = [[float(p), float(s)] for p, s in data.get("a", [])]

        if bids or asks:
            ob.update(bids, asks)
