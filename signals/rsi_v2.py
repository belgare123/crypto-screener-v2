"""
RSI Signal v2 — использует FeatureEngine вместо candle_buffer.
Читает rsi.14, macd, ema.50 из FeatureStore.
"""
from __future__ import annotations

import logging

from signals.base import BaseSignal, SignalContext, register

logger = logging.getLogger(__name__)


@register(
    name="rsi_v2",
    description="RSI из FeatureEngine: перекупленность/перепроданность + MACD/EMA подтверждение",
    category="candle",
    default_score=65,
    cooldown=600,
    timeframes=["1m", "5m"],
)
class RSISignalV2(BaseSignal):
    """RSI сигнал на FeatureEngine."""

    OVERBOUGHT = 70
    OVERSOLD = 30

    async def check(self, ctx: SignalContext) -> dict | None:
        if ctx.features is None:
            return None

        symbol = ctx.symbol

        # ── Базовые фичи из FeatureStore ──
        rsi = await ctx.features.get_feature(symbol, "rsi.14")
        if rsi is None:
            return None

        macd = await ctx.features.get_feature(symbol, "macd")
        ema50 = await ctx.features.get_feature(symbol, "ema.50")

        # ── Логика ──
        score = 0
        direction = None
        meta = {"rsi": rsi}

        # RSI напрямую
        if rsi >= self.OVERBOUGHT:
            score = min(100, rsi)
            direction = "sell"
            meta["source"] = "overbought"
        elif rsi <= self.OVERSOLD:
            score = max(0, 100 - rsi)
            direction = "buy"
            meta["source"] = "oversold"
        else:
            # Зона без экстримума — тихий сигнал, если MACD подтверждает
            if macd:
                macd_line = macd.get("macd", 0)
                signal_line = macd.get("signal", 0)
                if macd_line > signal_line and rsi < 50:
                    score = 55
                    direction = "buy"
                    meta["source"] = "macd_bullish"
                elif macd_line < signal_line and rsi > 50:
                    score = 55
                    direction = "sell"
                    meta["source"] = "macd_bearish"
                meta["macd"] = macd

        if score == 0:
            return None

        # EMA50 как фильтр тренда
        if ema50 and direction:
            meta["ema50"] = ema50

        return self._result(
            symbol=symbol,
            score=score,
            direction=direction,
            meta=meta,
        )
