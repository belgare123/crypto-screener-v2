"""
Indicators Feature Calculator — технические индикаторы.
Берёт данные из OHLCV калькулятора (через FeatureStore) и вычисляет:
- EMA(8, 21, 50, 200)
- RSI(14)
- ADX(14)
- MACD(12, 26, 9)
- Bollinger Bands(20, 2)

Принцип: ни один сигнал не считает индикаторы самостоятельно.
Все индикаторы — из FeatureStore.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from typing import Any

from core import Event
from core.features.base import BaseFeatureCalculator

logger = logging.getLogger(__name__)


class IndicatorsFeatureCalculator(BaseFeatureCalculator):
    """
    Калькулятор технических индикаторов.
    Зависит от OHLCVFeatureCalculator (читает ohlcv.1m.buffer из FeatureStore).
    """

    event_channels = ["candles.*"]  # пересчитываем индикаторы на каждой свече
    feature_names = [
        "ema.8",
        "ema.21",
        "ema.50",
        "ema.200",
        "rsi.14",
        "adx.14",
        "macd",
        "bb.20.2",
    ]
    default_ttl = 60.0  # пересчитываем раз в минуту
    priority = 50

    def __init__(self, feature_store=None):
        super().__init__(feature_store)
        # Локальный кэш значений для быстрого доступа внутри одного тика
        self._prev_values: dict[str, dict[str, float]] = {}

    async def compute(self, symbol: str, event: Event | None = None) -> dict:
        """Вычислить все индикаторы для symbol на основе OHLCV."""
        buf = await self._store.get(symbol, "ohlcv.1m.buffer")
        if not buf or len(buf) < 50:
            return {}

        closes = [c["close"] for c in buf]
        highs = [c["high"] for c in buf]
        lows = [c["low"] for c in buf]

        features = {}

        # ── EMA ──
        for period in (8, 21, 50, 200):
            ema = self._calc_ema(closes, period)
            if ema is not None:
                features[f"ema.{period}"] = round(ema, 4)

        # ── RSI(14) ──
        rsi = self._calc_rsi(closes, 14)
        if rsi is not None:
            features["rsi.14"] = round(rsi, 2)

        # ── ADX(14) ──
        adx = self._calc_adx(highs, lows, closes, 14)
        if adx is not None:
            features["adx.14"] = round(adx, 2)

        # ── MACD(12, 26, 9) ──
        macd = self._calc_macd(closes, 12, 26, 9)
        if macd is not None:
            features["macd"] = macd  # dict с macd, signal, histogram

        # ── Bollinger Bands(20, 2) ──
        bb = self._calc_bb(closes, 20, 2)
        if bb is not None:
            features["bb.20.2"] = bb  # dict с upper, middle, lower, bandwidth

        return features

    # ── EMA ──

    def _calc_ema(self, closes: list[float], period: int) -> float | None:
        """EMA(period) — экспоненциальная скользящая средняя."""
        if len(closes) < period * 2:
            return None
        multiplier = 2 / (period + 1)
        # Начальное SMA
        ema = sum(closes[-period * 2:-period]) / period
        for price in closes[-period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    # ── RSI ──

    def _calc_rsi(self, closes: list[float], period: int = 14) -> float | None:
        """RSI(period)."""
        if len(closes) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(len(closes) - period, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    # ── ADX ──

    def _calc_adx(
        self, highs: list[float], lows: list[float], closes: list[float], period: int = 14
    ) -> float | None:
        """ADX(period) — Average Directional Index."""
        if len(closes) < period * 2:
            return None
        trs = []
        up_moves = []
        down_moves = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
            up_move = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]
            up_moves.append(max(up_move, 0))
            down_moves.append(max(down_move, 0))

        if len(trs) < period:
            return None

        # Берем последние period значений
        recent_trs = trs[-period:]
        recent_up = up_moves[-period:]
        recent_down = down_moves[-period:]

        avg_tr = sum(recent_trs) / period
        avg_up = sum(recent_up) / period
        avg_down = sum(recent_down) / period

        if avg_tr == 0:
            return 0.0

        di_plus = (avg_up / avg_tr) * 100
        di_minus = (avg_down / avg_tr) * 100
        dx = abs(di_plus - di_minus) / (di_plus + di_minus) * 100 if (di_plus + di_minus) > 0 else 0
        return dx

    # ── MACD ──

    def _calc_macd(
        self, closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
    ) -> dict | None:
        """MACD(fast, slow, signal)."""
        if len(closes) < slow + signal:
            return None
        ema_fast = self._calc_ema(closes, fast)
        ema_slow = self._calc_ema(closes, slow)
        if ema_fast is None or ema_slow is None:
            return None
        macd_line = ema_fast - ema_slow
        # Signal line — упрощённо: EMA от MACD (тут нужна история MACD)
        # В реальности надо считать серию MACD, здесь аппроксимация
        # Для точности пересчитываем на последних N точках
        macd_values = []
        for i in range(len(closes) - signal, len(closes)):
            chunk = closes[:i + 1]
            ef = self._calc_ema(chunk, fast)
            es = self._calc_ema(chunk, slow)
            if ef is not None and es is not None:
                macd_values.append(ef - es)
        if not macd_values:
            return None
        signal_line = sum(macd_values) / len(macd_values)
        histogram = macd_line - signal_line
        return {
            "macd": round(macd_line, 4),
            "signal": round(signal_line, 4),
            "histogram": round(histogram, 4),
        }

    # ── Bollinger Bands ──

    def _calc_bb(
        self, closes: list[float], period: int = 20, std_mult: float = 2.0
    ) -> dict | None:
        """Bollinger Bands(period, std_mult)."""
        if len(closes) < period:
            return None
        recent = closes[-period:]
        sma = sum(recent) / period
        variance = sum((x - sma) ** 2 for x in recent) / period
        std = math.sqrt(variance)
        return {
            "upper": round(sma + std_mult * std, 4),
            "middle": round(sma, 4),
            "lower": round(sma - std_mult * std, 4),
            "bandwidth": round((std * std_mult * 2) / sma, 6) if sma > 0 else 0,
        }
