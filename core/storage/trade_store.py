import asyncio
from collections import deque
from typing import Dict, List, Optional, Any

from .base import DataStore

TradeDict = Dict[str, Any]  # price, size, side, timestamp, ...


class TradeStore(DataStore):
    """Хранилище для сделок (in-memory deque {symbol → deque[TradeDict]})."""

    def __init__(self, maxlen: int = 1000):
        self._data: Dict[str, deque] = {}
        self._maxlen = maxlen
        self._lock = asyncio.Lock()

    # ── DataStore interface ────────────────────────────────────

    async def get(self, symbol: str, **kwargs) -> Optional[List[TradeDict]]:
        """Вернуть все сделки по символу (копия списка)."""
        async with self._lock:
            if symbol not in self._data:
                return None
            return list(self._data[symbol])

    async def put(self, symbol: str, data: TradeDict, **kwargs) -> None:
        """Добавить одну сделку."""
        async with self._lock:
            if symbol not in self._data:
                self._data[symbol] = deque(maxlen=self._maxlen)
            self._data[symbol].append(data)

    async def delete(self, symbol: str) -> None:
        async with self._lock:
            self._data.pop(symbol, None)

    # ── Специфичные методы ─────────────────────────────────────

    async def put_trade(self, symbol: str, trade: TradeDict) -> None:
        """Добавить одну сделку (псевдоним)."""
        await self.put(symbol, trade)

    async def get_recent(self, symbol: str, limit: int = 100) -> List[TradeDict]:
        """Вернуть последние N сделок."""
        async with self._lock:
            if symbol not in self._data:
                return []
            return list(self._data[symbol])[-limit:]

    async def clear_symbol(self, symbol: str) -> None:
        """Очистить все сделки по символу."""
        async with self._lock:
            self._data.pop(symbol, None)
