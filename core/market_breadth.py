"""
Market Breadth Engine — ширина рынка.

Считает:
  - Сколько монет растёт / падает (change_24h > 0 / < 0)
  - % зелёных (Growing / Total)
  - Топ-5 гейнеров и лузеров
  - Среднее изменение
  - Сигнал при экстремальном breadth (90%+ / 20%-)

Источник данных: ticker_store.all (24h change)
Обновление: раз в 30 секунд (tick)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core import SignalResult

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Breadth Snapshot
# ──────────────────────────────────────────────

@dataclass
class BreadthSnapshot:
    timestamp: float = 0.0
    total: int = 0
    growing: int = 0        # change_24h > 0
    falling: int = 0        # change_24h < 0
    flat: int = 0           # change_24h == 0
    pct_green: float = 0.0  # growing / total * 100
    pct_red: float = 0.0    # falling / total * 100
    avg_change: float = 0.0  # среднее изменение по всем
    top_gainers: list[dict] = field(default_factory=list)  # [{symbol, change}, ...]
    top_losers: list[dict] = field(default_factory=list)   # [{symbol, change}, ...]
    regime: str = "neutral"  # extreme_green / green / neutral / red / extreme_red


def _classify_breadth(pct_green: float) -> str:
    """Определить режим рыночной широты."""
    if pct_green >= 85:
        return "extreme_green"
    elif pct_green >= 65:
        return "green"
    elif pct_green >= 40:
        return "neutral"
    elif pct_green >= 20:
        return "red"
    else:
        return "extreme_red"


# ──────────────────────────────────────────────
#  Market Breadth Engine
# ──────────────────────────────────────────────

class MarketBreadthEngine:
    """
    Движок ширины рынка.
    Использует ticker_store.all для вычисления метрик.

    Использование:
        engine = MarketBreadthEngine(ticker_getter)
        snap = engine.tick()  # вызывать раз в 30-60 секунд
    """

    def __init__(self, ticker_getter):
        """
        ticker_getter: callable() → dict[symbol, ticker_dict]
            ticker_dict должен иметь 'change_24h' или 'price24hPcnt'
        """
        self._ticker_getter = ticker_getter
        self._last_snapshot: BreadthSnapshot = BreadthSnapshot()
        self._min_change_pct = 0.01  # минимальное изменение для учёта

    @property
    def last_snapshot(self) -> BreadthSnapshot:
        return self._last_snapshot

    def tick(self) -> BreadthSnapshot:
        """Обновить метрики ширины рынка."""
        all_tickers = self._ticker_getter()
        if not all_tickers:
            return self._last_snapshot

        changes = []
        for sym, t in all_tickers.items():
            # Bybit: change_24h может быть в price24hPcnt (например, 0.0123 = +1.23%)
            raw = t.get("change_24h", t.get("price24hPcnt", 0))
            change = float(raw) if raw else 0.0
            # Бывает, что change в % (1.23) вместо десятичного (0.0123)
            # Определяем: если |raw| > 1.0, это % — делим на 100
            if abs(change) > 1.0:
                change = change / 100.0
            changes.append({"symbol": sym, "change": change})

        if not changes:
            return self._last_snapshot

        total = len(changes)
        growing = [c for c in changes if c["change"] > self._min_change_pct]
        falling = [c for c in changes if c["change"] < -self._min_change_pct]
        flat = [c for c in changes if abs(c["change"]) <= self._min_change_pct]

        pct_green = (len(growing) / total * 100) if total > 0 else 0.0
        pct_red = (len(falling) / total * 100) if total > 0 else 0.0
        avg_change = sum(c["change"] for c in changes) / total if total > 0 else 0.0

        # Топ-5 по изменению
        sorted_changes = sorted(changes, key=lambda c: abs(c["change"]), reverse=True)
        top_gainers = [c for c in sorted_changes if c["change"] > 0][:5]
        top_losers = [c for c in sorted_changes if c["change"] < 0][-5:]
        top_losers.reverse()

        self._last_snapshot = BreadthSnapshot(
            timestamp=time.time(),
            total=total,
            growing=len(growing),
            falling=len(falling),
            flat=len(flat),
            pct_green=round(pct_green, 1),
            pct_red=round(pct_red, 1),
            avg_change=round(avg_change * 100, 2),  # в %
            top_gainers=[{"symbol": g["symbol"], "change_pct": round(g["change"] * 100, 2)} for g in top_gainers],
            top_losers=[{"symbol": g["symbol"], "change_pct": round(g["change"] * 100, 2)} for g in top_losers],
            regime=_classify_breadth(pct_green),
        )

        return self._last_snapshot

    def to_signal(self) -> SignalResult | None:
        """
        Создать SignalResult если breadth экстремальный.
        Возвращает None при нормальных условиях.
        """
        snap = self._last_snapshot
        if snap.total == 0:
            return None

        if snap.regime == "extreme_green":
            score = 85
            direction = "buy"
            label = "широкий рынок: почти все монеты зелёные"
        elif snap.regime == "extreme_red":
            score = 85
            direction = "sell"
            label = "широкий рынок: почти все монеты красные"
        elif snap.regime == "green":
            score = 65
            direction = "buy"
            label = "рынок преимущественно зелёный"
        elif snap.regime == "red":
            score = 65
            direction = "sell"
            label = "рынок преимущественно красный"
        else:
            return None

        return SignalResult(
            signal_name="market_breadth",
            symbol="MARKET",
            exchange="bybit",
            score=score,
            direction=direction,
            meta={
                "total": snap.total,
                "growing": snap.growing,
                "falling": snap.falling,
                "pct_green": snap.pct_green,
                "pct_red": snap.pct_red,
                "avg_change": snap.avg_change,
                "regime": snap.regime,
                "top_gainers": snap.top_gainers,
                "top_losers": snap.top_losers,
                "description": label,
            },
            ts=snap.timestamp,
            cooldown=1800,  # 30 мин
        )

    def get_info(self) -> dict[str, Any]:
        """Полный отчёт для лога / меню."""
        snap = self._last_snapshot
        return {
            "total": snap.total,
            "growing": snap.growing,
            "falling": snap.falling,
            "pct_green": snap.pct_green,
            "pct_red": snap.pct_red,
            "avg_change_pct": snap.avg_change,
            "regime": snap.regime,
            "top_gainers": snap.top_gainers,
            "top_losers": snap.top_losers,
        }


# singleton
_breadth_engine: MarketBreadthEngine | None = None


def get_breadth_engine(ticker_getter=None) -> MarketBreadthEngine:
    global _breadth_engine
    if _breadth_engine is None and ticker_getter is not None:
        _breadth_engine = MarketBreadthEngine(ticker_getter)
    return _breadth_engine
