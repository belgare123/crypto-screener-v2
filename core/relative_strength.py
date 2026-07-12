"""
Relative Strength Engine — сравнивает активы относительно BTC и друг друга.

RS = цена_символа / цена_BTC (ratio)
RS Momentum = изменение RS за N периодов
Ранжирование: топ-3 / топ-5 сильнейших и слабейших
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
#  RS Snapshot
# ──────────────────────────────────────────────

@dataclass
class RSSnapshot:
    timestamp: float = 0.0
    rankings: list[dict] = field(default_factory=list)    # [{symbol, rs, rs_5m, rs_15m, rank_5m, rank_15m}, ...]


# ──────────────────────────────────────────────
#  RS Engine
# ──────────────────────────────────────────────

class RelativeStrengthEngine:
    MONITORED_SYMBOLS: tuple[str, ...] = (
        "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
        "XRP/USDT:USDT", "DOGE/USDT:USDT", "ADA/USDT:USDT",
        "AVAX/USDT:USDT", "DOT/USDT:USDT", "LINK/USDT:USDT",
        "SUI/USDT:USDT",
    )

    def __init__(self, candle_buffer):
        self._candle_buffer = candle_buffer
        self._initialized = False
        # Кеш последних цен по символам
        self._prices: dict[str, float] = {}
        # RS history (по 5м закрытиям)
        self._rs: dict[str, list[float]] = defaultdict(list)
        self._maxlen = 100

    def update(self, prices: dict[str, float]) -> RSSnapshot | None:
        """Сохранить цены и пересчитать RS."""
        if not prices or "BTC/USDT:USDT" not in prices:
            return None

        btc_price = prices.get("BTC/USDT:USDT", 0)
        if btc_price <= 0:
            return None

        for sym, price in prices.items():
            if price and price > 0:
                self._prices[sym] = price
                if sym != "BTC/USDT:USDT":
                    rs_val = price / btc_price
                    self._rs[sym].append(rs_val)
                    if len(self._rs[sym]) > self._maxlen:
                        self._rs[sym] = self._rs[sym][-self._maxlen:]

        self._initialized = True
        return self._build_snapshot()

    def _build_snapshot(self) -> RSSnapshot:
        """Построить ранжирование активов по RS."""
        rankings = []

        for sym in self.MONITORED_SYMBOLS:
            if sym == "BTC/USDT:USDT":
                continue
            rs_vals = self._rs.get(sym, [])
            if len(rs_vals) < 2:
                continue

            current_rs = rs_vals[-1]
            rs_5m = current_rs / rs_vals[max(0, len(rs_vals)-6)] - 1 if len(rs_vals) >= 6 else 0
            rs_15m = current_rs / rs_vals[max(0, len(rs_vals)-16)] - 1 if len(rs_vals) >= 16 else 0

            rankings.append({
                "symbol": sym,
                "rs": round(current_rs, 6),
                "rs_5m_pct": round(rs_5m * 100, 2),
                "rs_15m_pct": round(rs_15m * 100, 2),
            })

        # Ранжируем по 5м и 15м изменению
        rankings.sort(key=lambda r: r.get("rs_5m_pct", 0), reverse=True)
        for i, r in enumerate(rankings):
            r["rank_5m"] = i + 1

        rankings.sort(key=lambda r: r.get("rs_15m_pct", 0), reverse=True)
        for i, r in enumerate(rankings):
            r["rank_15m"] = i + 1

        # Восстанавливаем сортировку по символу
        rankings.sort(key=lambda r: r.get("rank_5m", 99))

        return RSSnapshot(timestamp=time.time(), rankings=rankings)

    def get_rs(self, symbol: str, lookback: int = 6) -> tuple[float | None, float | None]:
        """(current_rs, rs_change_pct) для символа за lookback сэмплов."""
        rs_vals = self._rs.get(symbol, [])
        if len(rs_vals) < 2:
            return None, None
        current = rs_vals[-1]
        prev = rs_vals[max(0, len(rs_vals) - lookback - 1)]
        change = (current / prev - 1) * 100 if prev else 0
        return current, round(change, 2)

    def get_ranking(self, metric: str = "rs_5m_pct", top_n: int = 3) -> tuple[list[dict], list[dict]]:
        """(top_assets, bottom_assets) по метрике RS."""
        snap = self._build_snapshot()
        ranked = sorted(snap.rankings, key=lambda r: r.get(metric, 0), reverse=True)
        top = ranked[:top_n]
        bottom = ranked[-top_n:] if ranked else []
        bottom.reverse()
        return top, bottom


# singleton
_rs_engine: RelativeStrengthEngine | None = None


def get_rs_engine(candle_buffer=None) -> RelativeStrengthEngine:
    global _rs_engine
    if _rs_engine is None and candle_buffer is not None:
        _rs_engine = RelativeStrengthEngine(candle_buffer)
    return _rs_engine
