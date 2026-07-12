import asyncio
from typing import Dict, List, Optional, Any

from .base import DataStore

TickerDict = Dict[str, Any]  # symbol, price, volume_24h, change_24h, turnover, ...


class TickerStore(DataStore):
    """Хранилище для тикеров (in-memory dict {symbol → ticker})."""

    def __init__(self):
        self._data: Dict[str, TickerDict] = {}
        self._lock = asyncio.Lock()

    # ── DataStore interface ────────────────────────────────────

    async def get(self, symbol: str, **kwargs) -> Optional[TickerDict]:
        async with self._lock:
            return self._data.get(symbol)

    async def put(self, symbol: str, data: TickerDict, **kwargs) -> None:
        async with self._lock:
            self._data[symbol] = data

    async def delete(self, symbol: str) -> None:
        async with self._lock:
            self._data.pop(symbol, None)

    # --- Специфичные методы ---

    async def all(self) -> Dict[str, TickerDict]:
        """Вернуть копию всех тикеров."""
        async with self._lock:
            return dict(self._data)

    async def get_top(
        self, by: str = "turnover", n: int = 10
    ) -> List[TickerDict]:
        """
        Вернуть топ N по указанному полю.
        Поле должно существовать в TickerDict.
        """
        async with self._lock:
            if not self._data:
                return []
            sorted_items = sorted(
                self._data.items(),
                key=lambda item: item[1].get(by, 0),
                reverse=True,
            )
            return [item[1] for item in sorted_items[:n]]

    # ── Синхронный доступ (для legacy getter callback) ──────────

    def all_sync(self) -> Dict[str, TickerDict]:
        """Вернуть все тикеры синхронно (без блокировки)."""
        return dict(self._data)

    def get_sync(self, symbol: str) -> Optional[TickerDict]:
        """Синхронно получить тикер по символу."""
        return self._data.get(symbol)
