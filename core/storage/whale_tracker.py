"""WhaleTracker — отслеживает крупные сделки (Whale) и Cumulative Volume Delta (CVD)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Any


class WhaleTracker:
    """Ищет крупные сделки: $100K, $250K, $500K, $1M+.

    Хранит скользящее окно трейдов для расчёта Delta Volume (CVD).
    """

    WHALE_THRESHOLDS = [100_000, 250_000, 500_000, 1_000_000]

    def __init__(self, window_minutes: int = 60):
        self._trades: dict[str, deque] = defaultdict(lambda: deque(maxlen=10000))
        self._window = window_minutes * 60

    def add_trade(self, symbol: str, trade: dict[str, Any]) -> None:
        ts = trade.get("timestamp", time.time())
        self._trades[symbol].append({"ts": ts, **trade})

    def get_whales(
        self, symbol: str, threshold: float = 100_000
    ) -> list[dict[str, Any]]:
        """Вернуть все whale-сделки за последние N минут."""
        cutoff = time.time() - self._window
        whales = []
        for t in self._trades[symbol]:
            if t.get("ts", 0) > cutoff and t.get("notional", 0) >= threshold:
                whales.append(t)
        return whales

    def get_whale_summary(self, symbol: str) -> dict[str, Any] | None:
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

    def clear(self, symbol: str | None = None) -> None:
        if symbol:
            self._trades.pop(symbol, None)
        else:
            self._trades.clear()


# глобальный синглтон
_whale_tracker: WhaleTracker | None = None


def get_whale_tracker() -> WhaleTracker:
    global _whale_tracker
    if _whale_tracker is None:
        _whale_tracker = WhaleTracker(window_minutes=60)
    return _whale_tracker
