"""Trade Scanner — агрегирует трейды, ищет крупные сделки (Whale)."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from core import Event, SignalResult, get_bus
from scanner import BaseScanner


class WhaleTracker:
    """
    Ищет крупные сделки: $100K, $250K, $500K, $1M+.
    Хранит скользящее окно трейдов для расчёта Delta Volume (CVD).
    """

    WHALE_THRESHOLDS = [100_000, 250_000, 500_000, 1_000_000]

    def __init__(self, window_minutes: int = 60):
        self._trades: dict[str, deque] = defaultdict(lambda: deque(maxlen=10000))
        self._window = window_minutes * 60

    def add_trade(self, symbol: str, trade: dict):
        ts = trade.get("timestamp", time.time())
        self._trades[symbol].append({"ts": ts, **trade})

    def get_whales(self, symbol: str, threshold: float = 100_000) -> list[dict]:
        """Вернуть все whale-сделки за последние N минут."""
        cutoff = time.time() - self._window
        whales = []
        for t in self._trades[symbol]:
            if t.get("ts", 0) > cutoff and t.get("notional", 0) >= threshold:
                whales.append(t)
        return whales

    def get_whale_summary(self, symbol: str) -> dict | None:
        """Последняя whale-сделка."""
        cutoff = time.time() - self._window
        for t in reversed(self._trades[symbol]):
            if t.get("ts", 0) > cutoff and t.get("notional", 0) >= 100_000:
                return t
        return None

    def get_cvd(self, symbol: str) -> float:
        """Cumulative Volume Delta за последние N минут."""
        cutoff = time.time() - self._window
        delta = 0.0
        for t in self._trades[symbol]:
            if t.get("ts", 0) > cutoff:
                side = t.get("side", "")
                vol = float(t.get("notional", 0))
                if side == "buy":
                    delta += vol
                elif side == "sell":
                    delta -= vol
        return delta

    def clear(self, symbol: str | None = None):
        if symbol:
            self._trades.pop(symbol, None)
        else:
            self._trades.clear()


# глобальный трекер
whale_tracker = WhaleTracker(window_minutes=60)


class TradeScanner(BaseScanner):
    """Слушает трейды, складывает в WhaleTracker."""

    name = "trades"
    channel = "trades.*"

    async def process(self, event: Event):
        data = event.data
        # Bybit publicTrade: data — список трейдов [{...}, ...] или один dict
        trades = data if isinstance(data, list) else [data]
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            price = float(trade.get("price", trade.get("p", 0)))
            size = float(trade.get("size", trade.get("v", 0)))
            notional = abs(price * size)
            side = trade.get("side", trade.get("S", "buy")).lower()
            # Bybit: "Buy"/"Sell" → "buy"/"sell"
            if side in ("buy", "sell"):
                pass
            elif side in ("b", "buy"):
                side = "buy"
            elif side in ("s", "sell"):
                side = "sell"
            else:
                side = "buy"
            normalized = {
                "symbol": event.symbol,
                "exchange": event.exchange,
                "timestamp": event.ts / 1000,
                "price": price,
                "size": size,
                "notional": notional,
                "side": side,
                "type": trade.get("type", "market"),
            }
            whale_tracker.add_trade(event.symbol, normalized)
