"""
Consensus Signal v2 — мульти-индикаторное голосование на FeatureEngine.
Собирает RSI + MACD + EMA + ADX + Regime из FeatureStore и голосует.
"""
from __future__ import annotations

import asyncio
import logging

from signals.base import BaseSignal, SignalContext, register

logger = logging.getLogger(__name__)


@register(
    name="consensus_v2",
    description="Консенсус: голосование по всем индикаторам FeatureEngine",
    category="candle",
    default_score=70,
    cooldown=600,
    timeframes=["1m", "5m", "15m"],
)
class ConsensusSignalV2(BaseSignal):
    """
    Мульти-индикаторный консенсус.
    Каждый индикатор голосует bullish/bearish.
    Итоговый score = доля победившего лагеря.
    """

    async def check(self, ctx: SignalContext) -> dict | None:
        if ctx.features is None:
            return None

        symbol = ctx.symbol

        # ── Параллельно читаем все фичи ──
        rsi, macd, ema8, ema21, bb, adx, regime_trend = await asyncio.gather(
            ctx.features.get_feature(symbol, "rsi.14"),
            ctx.features.get_feature(symbol, "macd"),
            ctx.features.get_feature(symbol, "ema.8"),
            ctx.features.get_feature(symbol, "ema.21"),
            ctx.features.get_feature(symbol, "bb.20.2"),
            ctx.features.get_feature(symbol, "adx.14"),
            ctx.features.get_feature(symbol, "regime.trend"),
            return_exceptions=True,
        )

        # Маскируем исключения как None
        for v in (rsi, macd, ema8, ema21, bb, adx, regime_trend):
            if isinstance(v, Exception):
                pass  # игнорируем сбойную фичу

        votes = {"bullish": 0, "bearish": 0}
        weights = {"bullish": 0.0, "bearish": 0.0}
        meta = {}

        # ── 1) RSI(14) ──
        if isinstance(rsi, (int, float)):
            meta["rsi"] = rsi
            if rsi >= 55:
                votes["bullish"] += 1
                weights["bullish"] += rsi / 100
            elif rsi <= 45:
                votes["bearish"] += 1
                weights["bearish"] += (100 - rsi) / 100
            if rsi >= 70:
                votes["bullish"] += 1  # extra vote
                weights["bullish"] += 0.5
            elif rsi <= 30:
                votes["bearish"] += 1
                weights["bearish"] += 0.5

        # ── 2) MACD ──
        if isinstance(macd, dict):
            meta["macd"] = {
                "macd": macd.get("macd"),
                "signal": macd.get("signal"),
                "hist": macd.get("histogram"),
            }
            macd_line = macd.get("macd", 0)
            signal_line = macd.get("signal", 0)
            if macd_line > signal_line:
                votes["bullish"] += 1
                weights["bullish"] += 1.0
                meta["macd_cross"] = "bullish"
            elif macd_line < signal_line:
                votes["bearish"] += 1
                weights["bearish"] += 1.0
                meta["macd_cross"] = "bearish"

            hist = macd.get("histogram", 0)
            if hist > 0:
                votes["bullish"] += 1
                weights["bullish"] += 0.5
            elif hist < 0:
                votes["bearish"] += 1
                weights["bearish"] += 0.5

        # ── 3) EMA-кроссовер ──
        if isinstance(ema8, (int, float)) and isinstance(ema21, (int, float)):
            meta["ema8"] = ema8
            meta["ema21"] = ema21
            if ema8 > ema21:
                votes["bullish"] += 2
                weights["bullish"] += 2.0
            else:
                votes["bearish"] += 2
                weights["bearish"] += 2.0

        # ── 4) BB — проверка положения цены ──
        if isinstance(bb, dict):
            meta["bb_width"] = bb.get("bandwidth")
            middle = bb.get("middle", 0)
            upper = bb.get("upper", 0)
            lower = bb.get("lower", 0)
            if middle and upper and lower:
                # Если цена у верхней полосы — бычий натиск
                if upper - lower > 0:
                    price_pos = (ema8 or middle)  # используем ema8 или middle как прокси
                    if isinstance(price_pos, (int, float)):
                        if price_pos > middle + (upper - middle) * 0.5:
                            votes["bullish"] += 1
                            weights["bullish"] += 0.5
                        elif price_pos < middle - (middle - lower) * 0.5:
                            votes["bearish"] += 1
                            weights["bearish"] += 0.5

        # ── 5) ADX(14) — сила тренда ──
        if isinstance(adx, (int, float)):
            meta["adx"] = adx
            if adx > 25:
                # Сильный тренд — усиливаем текущее направление
                if weights["bullish"] > weights["bearish"]:
                    votes["bullish"] += 1
                    weights["bullish"] += 1.0
                elif weights["bearish"] > weights["bullish"]:
                    votes["bearish"] += 1
                    weights["bearish"] += 1.0
            elif adx < 20:
                # Слабый тренд — снижаем уверенность
                pass

        # ── 6) Regime Trend ──
        if isinstance(regime_trend, str):
            meta["regime_trend"] = regime_trend
            if regime_trend == "bull":
                votes["bullish"] += 1
                weights["bullish"] += 1.0
            elif regime_trend == "bear":
                votes["bearish"] += 1
                weights["bearish"] += 1.0
            # flat — нейтрально

        # ── Итог ──
        total_votes = votes["bullish"] + votes["bearish"]
        if total_votes < 3:
            return None

        direction = "buy" if weights["bullish"] > weights["bearish"] else "sell"
        max_weight = max(weights["bullish"], weights["bearish"])
        total_weight = weights["bullish"] + weights["bearish"]
        score = 40 + (max_weight / total_weight if total_weight > 0 else 0) * 60

        meta["bullish_votes"] = votes["bullish"]
        meta["bearish_votes"] = votes["bearish"]
        meta["total_votes"] = total_votes
        meta["consensus"] = direction

        logger.debug(
            "[consensus_v2] %s score=%.0f dir=%s votes=%d:%d rsi=%s adx=%s regime=%s",
            symbol, score, direction,
            votes["bullish"], votes["bearish"],
            rsi if isinstance(rsi, (int, float)) else "N/A",
            adx if isinstance(adx, (int, float)) else "N/A",
            regime_trend if isinstance(regime_trend, str) else "N/A",
        )

        return self._result(
            symbol=symbol,
            score=min(100, int(score)),
            direction=direction,
            meta=meta,
        )
