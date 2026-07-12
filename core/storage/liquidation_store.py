"""LiquidationStore — хранит ликвидации для последующего анализа сигналов."""

from __future__ import annotations

import time
from typing import Any


class LiquidationStore:
    """Хранит последние N ликвидаций для всех символов."""

    def __init__(self, maxlen: int = 1000):
        self._data: list[dict[str, Any]] = []
        self._maxlen = maxlen

    def add(self, symbol: str, exchange: str, data: dict) -> None:
        side = data.get("S") or data.get("side", "sell")
        if isinstance(side, str):
            side = side.lower()
        ts_raw = data.get("T", data.get("timestamp", time.time()))
        liq = {
            "symbol": symbol,
            "exchange": exchange,
            "side": "sell" if side in ("sell", "s") else "buy",
            "price": float(data.get("p", data.get("price", 0))),
            "size": float(data.get("v", data.get("size", 0))),
            "notional": float(data.get("v", data.get("size", 0)))
            * float(data.get("p", data.get("price", 0))),
            "ts": ts_raw / 1000
            if isinstance(ts_raw, (int, float)) and ts_raw > 1e10
            else ts_raw,
        }
        self._data.append(liq)
        if len(self._data) > self._maxlen:
            self._data = self._data[-self._maxlen :]

    def recent(self, minutes: int = 5) -> list[dict[str, Any]]:
        cutoff = time.time() - minutes * 60
        return [l for l in self._data if l["ts"] > cutoff]

    def total_volume(self, minutes: int = 5) -> float:
        return sum(l["notional"] for l in self.recent(minutes))


# глобальный синглтон
_liquidation_store: LiquidationStore | None = None


def get_liquidation_store() -> LiquidationStore:
    global _liquidation_store
    if _liquidation_store is None:
        _liquidation_store = LiquidationStore()
    return _liquidation_store
