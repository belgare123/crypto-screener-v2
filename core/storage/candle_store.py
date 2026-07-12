import asyncio
from collections import deque
from typing import Dict, List, Optional, Any

from .base import DataStore

CandleDict = Dict[str, Any]  # {timestamp, open, high, low, close, volume, ...}


class CandleStore(DataStore):
    """
    Хранилище для свечей OHLCV.

    Внутренняя структура: _data[symbol][interval] = deque(maxlen=maxlen)

    Каждая свеча — словарь с ключами:
      timestamp, open, high, low, close, volume
    (дополнительные поля допускаются).
    """

    def __init__(self, maxlen: int = 200):
        self._data: Dict[str, Dict[str, deque]] = {}
        self._maxlen = maxlen
        self._lock = asyncio.Lock()

    # ── DataStore interface ────────────────────────────────────

    async def get(
        self,
        key: str,
        timeframe: str | None = None,
        count: int | None = None,
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        """
        Получить свечи для символа.

        Поддерживает две формы:
        * Новая: get(symbol, interval="1m", limit=50) -> CandleStore API
        * Старая: get(symbol, "1", 60)                    -> CandleBuffer compat
        * Без interval → {interval: [CandleDict, ...]}
        """
        # backward compat: old callers pass (symbol, timeframe, count)
        interval = timeframe or kwargs.get("interval")
        limit = count or kwargs.get("limit", None)

        if interval is None:
            # Вернуть все интервалы
            async with self._lock:
                if key not in self._data:
                    return None
                return {
                    iv: list(self._data[key][iv])
                    for iv in self._data[key]
                }
        return await self.get_candles(key, interval, limit)

    async def put(self, symbol: str, data: CandleDict, **kwargs) -> None:
        """Сохранить свечу (псевдоним для put_candle)."""
        interval = kwargs.get("interval", "1m")
        await self.put_candle(symbol, interval, data)

    async def delete(self, symbol: str) -> None:
        """Удалить все данные по символу."""
        async with self._lock:
            self._data.pop(symbol, None)

    # ── Специфичные методы ─────────────────────────────────────

    async def put_candle(self, symbol: str, interval: str, candle: CandleDict) -> None:
        """Добавить одну свечу."""
        async with self._lock:
            if symbol not in self._data:
                self._data[symbol] = {}
            if interval not in self._data[symbol]:
                self._data[symbol][interval] = deque(maxlen=self._maxlen)
            self._data[symbol][interval].append(candle)

    async def put_candles(
        self, symbol: str, interval: str, candles: List[CandleDict]
    ) -> None:
        """Добавить список свечей (batch)."""
        async with self._lock:
            if symbol not in self._data:
                self._data[symbol] = {}
            if interval not in self._data[symbol]:
                self._data[symbol][interval] = deque(maxlen=self._maxlen)
            for c in candles:
                self._data[symbol][interval].append(c)

    async def get_candles(
        self, symbol: str, interval: str = "1m", limit: Optional[int] = None
    ) -> List[CandleDict]:
        """Вернуть список свечей (последние `limit`)."""
        async with self._lock:
            if symbol not in self._data or interval not in self._data[symbol]:
                return []
            candles = list(self._data[symbol][interval])
            if limit is not None:
                candles = candles[-limit:]
            return candles

    async def get_latest(self, symbol: str, interval: str = "1m") -> Optional[CandleDict]:
        """Вернуть последнюю свечу."""
        async with self._lock:
            if symbol not in self._data or interval not in self._data[symbol]:
                return None
            if not self._data[symbol][interval]:
                return None
            return self._data[symbol][interval][-1]

    async def clear_symbol(self, symbol: str) -> None:
        """Очистить все данные по символу (все интервалы)."""
        async with self._lock:
            self._data.pop(symbol, None)

    # ── Синхронный доступ (для legacy getter callback) ──────────

    def get_sync(self, symbol: str, interval: str = "1m", limit: Optional[int] = None) -> List[CandleDict]:
        """Синхронное чтение — без блокировки, для sync callbacks."""
        if symbol not in self._data or interval not in self._data.get(symbol, {}):
            return []
        candles = list(self._data[symbol][interval])
        if limit is not None:
            candles = candles[-limit:]
        return candles
