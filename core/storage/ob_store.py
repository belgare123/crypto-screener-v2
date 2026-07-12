import asyncio
import time
from typing import Dict, List, Tuple, Optional, Any

from .base import DataStore

PriceLevel = Tuple[float, float]  # (price, size)
OBSnapshot = Dict[str, Any]  # {"bids": [...], "asks": [...], "timestamp": float}


class OrderBookState:
    """Текущее состояние стакана для одного символа.

    Хранит bid/ask levels + методы анализа (стенки, спред, дисбаланс).
    """

    def __init__(self, max_levels: int = 20):
        self.bids: List[List[float]] = []  # [[price, size], ...]
        self.asks: List[List[float]] = []
        self.max_levels = max_levels
        self.updated_at: float = 0.0

    def update(self, bids: List[List[float]], asks: List[List[float]]):
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

    @staticmethod
    def _detect_level_walls(levels: List[List[float]], threshold_ratio: float) -> list:
        walls = []
        for i, (price, size) in enumerate(levels):
            if 0 < i < len(levels) - 1:
                avg_neighbors = (levels[i - 1][1] + levels[i + 1][1]) / 2
                if avg_neighbors > 0 and size / avg_neighbors >= threshold_ratio:
                    walls.append((price, size, round(size / avg_neighbors, 1)))
        return walls

    def detect_walls(self, threshold_ratio: float = 5.0) -> dict:
        """Ищет стенки: уровень, где объём в N+ раз больше соседних."""
        return {
            "bid_walls": self._detect_level_walls(self.bids, threshold_ratio),
            "ask_walls": self._detect_level_walls(self.asks, threshold_ratio),
            "imbalance": round(self.imbalance_ratio, 2),
        }

    def detect_iceberg(self, levels_similar: int = 3) -> bool:
        """Эвристика на айсберги — одинаковые объёмы на соседних уровнях."""
        for levels in [self.bids, self.asks]:
            sizes = [s for _, s in levels[:10]]
            if len(sizes) >= levels_similar:
                for i in range(len(sizes) - levels_similar + 1):
                    chunk = sizes[i:i + levels_similar]
                    if sum(chunk) > 10 and max(chunk) > 0 and (max(chunk) - min(chunk)) / max(chunk) < 0.1:
                        return True
        return False

    def detect_spoof(self) -> bool:
        """Эвристика на спуфинг: крупный ордер далеко от best bid/ask."""
        if len(self.asks) >= 5 and self.best_ask > 0:
            far_ask = self.asks[-1]
            far_pct = (far_ask[0] - self.best_ask) / self.best_ask * 100
            avg_ask = sum(a[1] for a in self.asks[:5]) / 5
            if far_pct > 0.5 and avg_ask > 0 and far_ask[1] > avg_ask * 3:
                return True
        if len(self.bids) >= 5 and self.best_bid > 0:
            far_bid = self.bids[-1]
            far_pct = (self.best_bid - far_bid[0]) / self.best_bid * 100
            avg_bid = sum(b[1] for b in self.bids[:5]) / 5
            if far_pct > 0.5 and avg_bid > 0 and far_bid[1] > avg_bid * 3:
                return True
        return False

    def to_dict(self) -> dict:
        """Сериализация в dict (совместимость с OBSnapshot)."""
        return {
            "bids": self.bids,
            "asks": self.asks,
            "timestamp": self.updated_at,
        }


class OBStore(DataStore):
    """Хранилище для стакана (in-memory OrderBookState)."""

    def __init__(self):
        self._data: Dict[str, OrderBookState] = {}
        self._lock = asyncio.Lock()

    # ── DataStore interface ────────────────────────────────────

    async def get(self, symbol: str, **kwargs) -> Optional[OrderBookState]:
        async with self._lock:
            return self._data.get(symbol)

    async def put(self, symbol: str, data: dict, **kwargs) -> None:
        """Сохранить полный snapshot из dict {bids, asks, timestamp}."""
        ob = OrderBookState()
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        ts = data.get("timestamp", time.time())
        ob.update(bids, asks)
        ob.updated_at = ts
        async with self._lock:
            self._data[symbol] = ob

    async def delete(self, symbol: str) -> None:
        async with self._lock:
            self._data.pop(symbol, None)

    # ── Специфичные методы ─────────────────────────────────────

    async def get_snapshot(self, symbol: str) -> Optional[OrderBookState]:
        """Вернуть OrderBookState для символа (алиас get)."""
        return await self.get(symbol)

    async def put_snapshot(
        self,
        symbol: str,
        bids: List[PriceLevel],
        asks: List[PriceLevel],
        ts: float,
    ) -> None:
        """Сохранить snapshot как OrderBookState."""
        ob = OrderBookState()
        ob.update(bids, asks)
        ob.updated_at = ts
        async with self._lock:
            self._data[symbol] = ob

    async def apply_delta(
        self, symbol: str, bids: List[PriceLevel], asks: List[PriceLevel]
    ) -> None:
        """Применить дельту — v1: полная замена, v2: мерж уровней."""
        ob = OrderBookState()
        ob.update(bids, asks)
        async with self._lock:
            self._data[symbol] = ob

    async def get_depth(
        self, symbol: str, levels: int = 10
    ) -> Optional[Dict[str, List[PriceLevel]]]:
        """Вернуть первые N уровней глубины."""
        snap = await self.get_snapshot(symbol)
        if not snap:
            return None
        return {
            "bids": snap.bids[:levels],
            "asks": snap.asks[:levels],
        }

    # ── Паттерн фабрики ────────────────────────────────────────

    def get_or_create(self, symbol: str, max_levels: int = 20) -> OrderBookState:
        """Вернуть или создать OrderBookState (sync)."""
        if symbol not in self._data:
            self._data[symbol] = OrderBookState(max_levels=max_levels)
        return self._data[symbol]

    # ── Синхронный доступ (для legacy getter callback) ──────────

    def get_sync(self, symbol: str) -> Optional[OrderBookState]:
        """Синхронное чтение (без блокировки)."""
        return self._data.get(symbol)


__all__ = ["OBStore", "OrderBookState"]
