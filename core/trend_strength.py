"""
Trend Strength — оценка силы и направления тренда для каждого символа.

Метрики (0-100%):
  - EMA alignment: цена vs EMA(9) vs EMA(26)
  - Slope: наклон цены за N свечей
  - HH/HL или LH/LL: структура тренда
  - ADX-подобная: расширение диапазона

Классификация:
  - strong_up / weak_up / sideways / weak_down / strong_down

Обновление: раз в 30 секунд для всех отслеживаемых символов.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from core import SignalResult

logger = logging.getLogger(__name__)


@dataclass
class TrendSnapshot:
    symbol: str = ""
    timestamp: float = 0.0
    trend_direction: str = "sideways"   # strong_up / weak_up / sideways / weak_down / strong_down
    trend_strength: float = 0.0         # 0-100%
    price: float = 0.0
    ema9: float = 0.0
    ema26: float = 0.0
    slope_5m: float = 0.0               # наклон на 5m
    hh_hl: str = ""                     # HHHL / LHLL / neutral
    higher_highs: int = 0               # кол-во последовательных HH
    higher_lows: int = 0                # кол-во последовательных HL
    signal_value: str = "neutral"


def ema(values: list[float], period: int) -> list[float]:
    """EMA периода."""
    if not values or len(values) < period:
        return []
    result = []
    multiplier = 2.0 / (period + 1)
    # SMA as first EMA
    ema_prev = sum(values[:period]) / period
    result.append(ema_prev)
    for v in values[period:]:
        ema_val = (v - ema_prev) * multiplier + ema_prev
        result.append(ema_val)
        ema_prev = ema_val
    return result


def _detect_hh_hl(prices: list[float], lookback: int = 5) -> tuple[str, int, int]:
    """Detect Higher High / Higher Low pattern."""
    if len(prices) < lookback * 2:
        return "neutral", 0, 0

    hh_count = 0
    hl_count = 0
    lh_count = 0
    ll_count = 0

    recent = prices[-lookback * 2:]
    mid = len(recent) // 2
    first_half = recent[:mid]
    second_half = recent[mid:]

    # Higher highs: max of second half > max of first half
    if max(second_half) > max(first_half):
        hh_count = 1

    # Higher lows: min of second half > min of first half
    if min(second_half) > min(first_half):
        hl_count = 1

    # Lower highs: max of second half < max of first half
    if max(second_half) < max(first_half):
        lh_count = 1

    # Lower lows: min of second half < min of first half
    if min(second_half) < min(first_half):
        ll_count = 1

    if hh_count and hl_count:
        return "HHHL", hh_count, hl_count
    elif lh_count and ll_count:
        return "LHLL", lh_count, ll_count
    elif hh_count:
        return "HH", hh_count, 0
    elif ll_count:
        return "LL", 0, ll_count
    return "neutral", 0, 0


class TrendStrengthEngine:
    """
    Оценка силы тренда по свечным данным.
    """

    def __init__(self, candle_getter: Callable):
        """
        candle_getter(symbol, timeframe, count) → list[dict]
        """
        self._candle_getter = candle_getter
        self._snapshots: dict[str, TrendSnapshot] = {}

    @property
    def last_snapshots(self) -> dict[str, TrendSnapshot]:
        return dict(self._snapshots)

    def get_snapshot(self, symbol: str) -> TrendSnapshot | None:
        return self._snapshots.get(symbol)

    def analyze(self, symbol: str) -> TrendSnapshot:
        """Проанализировать тренд для символа."""
        candles_1m = self._candle_getter(symbol, "1", 60)
        candles_5m = self._candle_getter(symbol, "5", 30)

        closes_1m = [float(c.get("close", 0)) for c in candles_1m if c.get("close")]
        closes_5m = [float(c.get("close", 0)) for c in candles_5m if c.get("close")]

        if len(closes_1m) < 20:
            return TrendSnapshot(symbol=symbol, timestamp=time.time())

        price = closes_1m[-1]

        # ---- EMA ----
        ema9_values = ema(closes_1m, 9)
        ema26_values = ema(closes_1m, 26)

        ema9_val = ema9_values[-1] if len(ema9_values) > 0 else price
        ema26_val = ema26_values[-1] if len(ema26_values) > 0 else price

        # ---- Slope ----
        slope = 0.0
        if len(closes_5m) >= 10:
            # Наклон за последние 10 свечей 5m
            x = list(range(10))
            y = closes_5m[-10:]
            n = len(x)
            slope = (n * sum(x[i] * y[i] for i in range(n)) - sum(x) * sum(y)) / \
                    (n * sum(xi ** 2 for xi in x) - sum(x) ** 2)

        # ---- HH/HL ----
        hh_hl_type, hh_count, hl_count = _detect_hh_hl(closes_1m, 5)

        # ---- ADX-подобная метрика (диапазон) ----
        # Отношение расширения диапазона к последнему тренду
        range_short = max(c["high"] for c in candles_1m[-5:]) - min(c["low"] for c in candles_1m[-5:])
        range_long = max(c["high"] for c in candles_1m[-20:]) - min(c["low"] for c in candles_1m[-20:])
        range_ratio = range_short / max(range_long, 0.0001)

        # ---- Собираем скоринг ----
        scores = {}
        weights = {}

        # EMA alignment score (0-100)
        if price > ema9_val > ema26_val:
            ema_score = 80 + min(20, (price / max(ema26_val, 0.0001) - 1) * 100)
            weights["ema"] = 0.30
        elif price > ema9_val:
            ema_score = 60
            weights["ema"] = 0.25
        elif price > ema26_val:
            ema_score = 50
            weights["ema"] = 0.20
        elif price < ema9_val < ema26_val:
            ema_score = 80 + min(20, (ema26_val / max(price, 0.0001) - 1) * 100)
            weights["ema"] = 0.30
        elif price < ema9_val:
            ema_score = 60
            weights["ema"] = 0.25
        else:
            ema_score = 30
            weights["ema"] = 0.20
        scores["ema"] = ema_score

        # Slope score (0-100)
        slope_score = min(100, max(0, abs(slope) * 10000))
        weights["slope"] = 0.25

        # HH/HL score (0-100)
        if hh_hl_type == "HHHL":
            hh_score = 80
        elif hh_hl_type == "HH":
            hh_score = 65
        elif hh_hl_type == "LHLL":
            hh_score = 20
        elif hh_hl_type == "LL":
            hh_score = 35
        else:
            hh_score = 50
        weights["hh_hl"] = 0.25

        # Range expansion score
        range_score = min(100, range_ratio * 100)
        if range_score > 50:
            weights["range"] = 0.20
        else:
            weights["range"] = 0.10
        scores["range"] = range_score

        # Weighted average
        total_weight = sum(weights.values())
        if total_weight > 0:
            combined = sum(scores[k] * weights[k] for k in scores) / total_weight
        else:
            combined = 50

        trend_strength = min(100, max(0, combined))

        # Determine direction
        if slope > 0 and hh_hl_type in ("HHHL", "HH"):
            direction = "strong_up" if trend_strength >= 65 else "weak_up"
        elif slope < 0 and hh_hl_type in ("LHLL", "LL"):
            direction = "strong_down" if trend_strength >= 65 else "weak_down"
        elif slope > 0:
            direction = "weak_up"
        elif slope < 0:
            direction = "weak_down"
        else:
            direction = "sideways"

        snap = TrendSnapshot(
            symbol=symbol,
            timestamp=time.time(),
            trend_direction=direction,
            trend_strength=round(trend_strength, 1),
            price=price,
            ema9=round(ema9_val, 4),
            ema26=round(ema26_val, 4),
            slope_5m=round(slope, 6),
            hh_hl=hh_hl_type,
            higher_highs=hh_count,
            higher_lows=hl_count,
            signal_value=self._classify_signal(direction, trend_strength),
        )

        self._snapshots[symbol] = snap
        return snap

    @staticmethod
    def _classify_signal(direction: str, strength: float) -> str:
        if direction in ("strong_up",) and strength >= 70:
            return "strong_buy"
        elif direction in ("weak_up",) and strength >= 50:
            return "buy"
        elif direction in ("strong_down",) and strength >= 70:
            return "strong_sell"
        elif direction in ("weak_down",) and strength >= 50:
            return "sell"
        return "neutral"

    def to_signal(self, symbol: str) -> SignalResult | None:
        """Сигнал при сильном тренде."""
        snap = self.get_snapshot(symbol)
        if not snap or snap.trend_strength == 0:
            return None

        if snap.trend_strength < 65:
            return None

        score = min(snap.trend_strength, 95)
        direction_map = {
            "strong_up": "buy",
            "weak_up": "buy",
            "strong_down": "sell",
            "weak_down": "sell",
            "sideways": "neutral",
        }
        direction = direction_map.get(snap.trend_direction, "neutral")

        return SignalResult(
            signal_name="trend_strength",
            symbol=symbol,
            exchange="bybit",
            score=round(score, 0),
            direction=direction,
            meta={
                "price": snap.price,
                "trend_direction": snap.trend_direction,
                "trend_strength": snap.trend_strength,
                "ema9": snap.ema9,
                "ema26": snap.ema26,
                "hh_hl": snap.hh_hl,
                "slope_5m": snap.slope_5m,
                "signal_value": snap.signal_value,
                "description": f"Тренд {snap.trend_direction} ({snap.trend_strength:.0f}%)",
            },
            ts=snap.timestamp,
            cooldown=3600,
        )

    def get_info(self, symbol: str) -> dict[str, Any]:
        snap = self.get_snapshot(symbol)
        if not snap:
            return {}
        return {
            "symbol": snap.symbol,
            "direction": snap.trend_direction,
            "strength": snap.trend_strength,
            "price": snap.price,
            "ema9": snap.ema9,
            "ema26": snap.ema26,
            "hh_hl": snap.hh_hl,
            "slope_5m": snap.slope_5m,
            "signal": snap.signal_value,
        }


_trend_engine: TrendStrengthEngine | None = None


def get_trend_engine(candle_getter=None) -> TrendStrengthEngine:
    global _trend_engine
    if _trend_engine is None and candle_getter is not None:
        _trend_engine = TrendStrengthEngine(candle_getter)
    return _trend_engine
