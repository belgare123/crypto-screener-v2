"""
Indicator Signal v2 — комбинированный сигнал на FeatureEngine.
Читает EMA-кроссовер + RSI + Bollinger Bands + MACD из FeatureStore.
"""
from __future__ import annotations

import logging

from signals.base import BaseSignal, SignalContext, register

logger = logging.getLogger(__name__)


@register(
    name="indicator_v2",
    description="Комбинированный: EMA-кросс + RSI + BB + MACD из FeatureEngine",
    category="candle",
    default_score=60,
    cooldown=300,
    timeframes=["1m", "5m"],
)
class IndicatorSignalV2(BaseSignal):
    """Комбинированный сигнал на FeatureEngine."""

    async def check(self, ctx: SignalContext) -> dict | None:
        if ctx.features is None:
            return None

        symbol = ctx.symbol

        # ── Читаем фичи из FeatureStore ──
        rsi = await ctx.features.get_feature(symbol, "rsi.14")
        ema8 = await ctx.features.get_feature(symbol, "ema.8")
        ema21 = await ctx.features.get_feature(symbol, "ema.21")
        macd = await ctx.features.get_feature(symbol, "macd")
        bb = await ctx.features.get_feature(symbol, "bb.20.2")

        if rsi is None and macd is None:
            return None

        # ── Голоса ──
        votes = {"bullish": 0, "bearish": 0}
        meta = {}

        # 1) EMA-кроссовер
        if ema8 is not None and ema21 is not None:
            meta["ema8"] = ema8
            meta["ema21"] = ema21
            if ema8 > ema21:
                votes["bullish"] += 2
                meta["ema_cross"] = "bullish"
            else:
                votes["bearish"] += 2
                meta["ema_cross"] = "bearish"

        # 2) RSI
        if rsi is not None:
            meta["rsi"] = rsi
            if rsi >= 55:
                votes["bullish"] += 1
            elif rsi <= 45:
                votes["bearish"] += 1
            # Экстримум — усиление
            if rsi >= 70:
                votes["bullish"] += 1
                meta["rsi_extreme"] = "overbought"
            elif rsi <= 30:
                votes["bearish"] += 1
                meta["rsi_extreme"] = "oversold"

        # 3) MACD
        if macd is not None:
            meta["macd"] = macd
            macd_line = macd.get("macd", 0)
            signal_line = macd.get("signal", 0)
            if macd_line > signal_line:
                votes["bullish"] += 1
            elif macd_line < signal_line:
                votes["bearish"] += 1
            # Гистограмма — ускорение
            hist = macd.get("histogram", 0)
            if hist > 0:
                votes["bullish"] += 1
            elif hist < 0:
                votes["bearish"] += 1

        # 4) Bollinger Bands — squeeze / width
        if bb is not None:
            meta["bb"] = bb
            bandwidth = bb.get("bandwidth", 0)
            if bandwidth < 0.01:
                meta["bb_state"] = "squeeze"
            elif bandwidth > 0.05:
                meta["bb_state"] = "expansion"

        # ── Итог ──
        total = votes["bullish"] + votes["bearish"]
        if total < 3:
            return None

        if votes["bullish"] == votes["bearish"]:
            return None  # нейтрально

        direction = "buy" if votes["bullish"] > votes["bearish"] else "sell"
        ratio = votes["bullish"] / total if direction == "buy" else votes["bearish"] / total
        score = 40 + ratio * 60  # 40–100

        meta["bullish_votes"] = votes["bullish"]
        meta["bearish_votes"] = votes["bearish"]
        meta["total_votes"] = total

        logger.debug(
            "[indicator_v2] %s score=%.0f dir=%s bullish=%d bearish=%d rsi=%s",
            symbol, score, direction, votes["bullish"], votes["bearish"],
            rsi if rsi is not None else "N/A",
        )

        return self._result(
            symbol=symbol,
            score=min(100, int(score)),
            direction=direction,
            meta=meta,
        )
