"""Ticker, Liquidation scanners."""

from __future__ import annotations

import logging
import time
from collections import defaultdict

from core import Event, get_bus
from scanner import BaseScanner

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Ticker Scanner — 24h объём, цена, изменение
# ──────────────────────────────────────────────

class TickerStore:
    """Хранит последние тикеры для каждого символа."""

    def __init__(self):
        self._data: dict[str, dict] = {}

    def update(self, symbol: str, ticker: dict):
        """Partial update — не затирает поля, которых нет в delta."""
        existing = self._data.get(symbol, {})
        mapped = {
            "last_price": ticker.get("lastPrice", existing.get("last_price", 0)),
            "volume_24h": ticker.get("volume24h", existing.get("volume_24h", 0)),
            "turnover_24h": ticker.get("turnover24h", ticker.get("quoteVolume", existing.get("turnover_24h", 0))),
            "change_24h": ticker.get("price24hPcnt", existing.get("change_24h", 0)),
            "high_24h": ticker.get("highPrice24h", existing.get("high_24h", 0)),
            "low_24h": ticker.get("lowPrice24h", existing.get("low_24h", 0)),
        }
        # Если пришло новое значение — конвертируем; иначе берём старое
        self._data[symbol] = {
            "symbol": symbol,
            "last_price": float(mapped["last_price"]) if mapped["last_price"] else existing.get("last_price", 0.0),
            "volume_24h": float(mapped["volume_24h"]) if mapped["volume_24h"] else existing.get("volume_24h", 0.0),
            "turnover_24h": float(mapped["turnover_24h"]) if mapped["turnover_24h"] else existing.get("turnover_24h", 0.0),
            "change_24h": float(mapped["change_24h"]) if mapped["change_24h"] else existing.get("change_24h", 0.0),
            "high_24h": float(mapped["high_24h"]) if mapped["high_24h"] else existing.get("high_24h", 0.0),
            "low_24h": float(mapped["low_24h"]) if mapped["low_24h"] else existing.get("low_24h", 0.0),
            "updated_at": time.time(),
        }

    def get(self, symbol: str) -> dict | None:
        return self._data.get(symbol)

    @property
    def all(self) -> dict[str, dict]:
        return dict(self._data)


ticker_store = TickerStore()


class TickerScanner(BaseScanner):
    name = "ticker"
    channel = "ticker.*"

    def __init__(self):
        super().__init__()

    async def process(self, event: Event):
        ticker_store.update(event.symbol, event.data)


# ──────────────────────────────────────────────
#  Liquidation Scanner
# ──────────────────────────────────────────────

class LiquidationStore:
    def __init__(self):
        self._data: list[dict] = []
        self._maxlen = 1000

    def add(self, symbol: str, exchange: str, data: dict):
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
            "notional": float(data.get("v", data.get("size", 0))) * float(data.get("p", data.get("price", 0))),
            "ts": ts_raw / 1000 if isinstance(ts_raw, (int, float)) and ts_raw > 1e10 else ts_raw,
        }
        self._data.append(liq)
        if len(self._data) > self._maxlen:
            self._data = self._data[-self._maxlen:]

    def recent(self, minutes: int = 5) -> list[dict]:
        cutoff = time.time() - minutes * 60
        return [l for l in self._data if l["ts"] > cutoff]

    def total_volume(self, minutes: int = 5) -> float:
        return sum(l["notional"] for l in self.recent(minutes))


liquidation_store = LiquidationStore()


class LiquidationScanner(BaseScanner):
    name = "liquidation"
    channel = "liquidation.*"

    async def process(self, event: Event):
        data = event.data
        items = data if isinstance(data, list) else [data]
        for liq in items:
            if not isinstance(liq, dict):
                continue
            liquidation_store.add(event.symbol, event.exchange, liq)
