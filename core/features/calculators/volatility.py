"""
Volatility & Regime Feature Calculator.
Вычисляет волатильность, рыночный режим и метрики риска.

Производит фичи:
- vol.atr_1m: float — ATR(14) на 1m
- vol.atr_5m: float — ATR(14) на 5m
- vol.atr_pct: float — ATR% от цены
- vol.regime: str — low / normal / high / extreme
- vol.regime_score: float — 0-100
- regime.trend: str — bull / bear / flat
- regime.volatility_state: str — compression / expansion
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core import Event
from core.features.base import BaseFeatureCalculator

logger = logging.getLogger(__name__)


class VolatilityFeatureCalculator(BaseFeatureCalculator):
    """
    Калькулятор волатильности и режима.
    Зависит от OHLCVFeatureCalculator (читает ohlcv из FeatureStore).
    """

    event_channels = ["candles.*"]  # пересчитываем волатильность на каждой свече
    feature_names = [
        "vol.atr_1m",
        "vol.atr_5m",
        "vol.atr_pct",
        "vol.regime",
        "vol.regime_score",
        "regime.trend",
        "regime.volatility_state",
    ]
    default_ttl = 60.0
    priority = 60

    def __init__(self, feature_store=None):
        super().__init__(feature_store)
        # Предыдущие состояния для определения тренда
        self._prev_regime: dict[str, str] = {}

    async def compute(self, symbol: str, event: Event | None = None) -> dict:
        """Вычислить волатильность и режим."""
        buf_1m = await self._store.get(symbol, "ohlcv.1m.buffer")
        if not buf_1m or len(buf_1m) < 20:
            return {}

        buf_5m = await self._store.get(symbol, "ohlcv.5m.buffer")
        ticker = await self._store.get(symbol, "ticker.last")

        closes = [c["close"] for c in buf_1m]
        highs = [c["high"] for c in buf_1m]
        lows = [c["low"] for c in buf_1m]
        current_price = closes[-1] if closes else 0.0

        # ATR(14) на 1m
        atr_1m = self._calc_atr(highs, lows, closes, 14)

        # ATR(14) на 5m
        atr_5m = 0.0
        if buf_5m and len(buf_5m) >= 15:
            h5 = [c["high"] for c in buf_5m]
            l5 = [c["low"] for c in buf_5m]
            c5 = [c["close"] for c in buf_5m]
            atr_5m = self._calc_atr(h5, l5, c5, 14)

        # ATR% от цены
        ref_atr = atr_5m or atr_1m
        atr_pct = (ref_atr / current_price * 100) if current_price > 0 else 0.0

        # Режим волатильности
        regime = self._classify_regime(atr_pct)
        prev = self._prev_regime.get(symbol)
        self._prev_regime[symbol] = regime

        # Score: 0-100
        regime_scores = {
            "extreme": 90,
            "high": 65,
            "normal": 35,
            "low": 10,
        }
        regime_score = regime_scores.get(regime, 35)

        # Тренд (на основе EMA-8 vs EMA-21)
        trend = self._calc_trend(closes)

        # Состояние волатильности (expansion/compression)
        vol_state = self._calc_volatility_state(atr_pct, prev, regime)

        return {
            "vol.atr_1m": round(atr_1m, 4),
            "vol.atr_5m": round(atr_5m, 4),
            "vol.atr_pct": round(atr_pct, 4),
            "vol.regime": regime,
            "vol.regime_score": regime_score,
            "regime.trend": trend,
            "regime.volatility_state": vol_state,
        }

    def _calc_atr(
        self, highs: list[float], lows: list[float], closes: list[float], period: int = 14
    ) -> float:
        """ATR(period)."""
        if len(closes) < period + 1:
            return 0.0
        trs = []
        for i in range(-period, 0):
            prev_close = closes[i - 1]
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - prev_close),
                abs(lows[i] - prev_close),
            )
            trs.append(tr)
        if not trs:
            return 0.0
        return sum(trs) / len(trs)

    def _classify_regime(self, atr_pct: float) -> str:
        """Классификация режима волатильности по ATR%."""
        if atr_pct < 0.3:
            return "low"
        elif atr_pct < 1.0:
            return "normal"
        elif atr_pct < 2.5:
            return "high"
        else:
            return "extreme"

    def _calc_trend(self, closes: list[float], fast: int = 8, slow: int = 21) -> str:
        """Определить тренд по EMA-8 vs EMA-21."""
        if len(closes) < slow:
            return "flat"
        ema_fast = self._calc_ema(closes, fast)
        ema_slow = self._calc_ema(closes, slow)
        if ema_fast is None or ema_slow is None:
            return "flat"
        diff_pct = (ema_fast - ema_slow) / ema_slow * 100
        if diff_pct > 0.5:
            return "bull"
        elif diff_pct < -0.5:
            return "bear"
        return "flat"

    def _calc_ema(self, closes: list[float], period: int) -> float | None:
        """EMA(period)."""
        if len(closes) < period * 2:
            return None
        multiplier = 2 / (period + 1)
        ema = sum(closes[-period * 2:-period]) / period
        for price in closes[-period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _calc_volatility_state(
        self, atr_pct: float, prev_regime: str | None, current_regime: str
    ) -> str:
        """Определить, сжимается или расширяется волатильность."""
        if prev_regime is None:
            return "stable"
        regimes = ["low", "normal", "high", "extreme"]
        prev_idx = regimes.index(prev_regime) if prev_regime in regimes else 1
        curr_idx = regimes.index(current_regime) if current_regime in regimes else 1
        if curr_idx > prev_idx:
            return "expansion"
        elif curr_idx < prev_idx:
            return "compression"
        return "stable"
