"""
Whale Signal v2 — крупные сделки, использует FeatureEngine.
Мигрирован с прямого чтения ctx.whale_trades на FeatureEngine.
"""

from __future__ import annotations

import time

from signals.base import BaseSignal, SignalContext, register


@register(
    name="whale_v2",
    description="Крупные сделки: $100K, $250K, $500K, $1M+ (через FeatureEngine)",
    category="whale",
    default_score=80,
    cooldown=900,
    timeframes=["1m", "5m"],
)
class WhaleSignalV2(BaseSignal):
    """
    WhaleSignal, использующий FeatureEngine вместо прямого контекста.
    Берёт whale-фичи из FeatureStore через ctx.features.

    Преимущества:
    - Не дублирует логику расчёта CVD/агрессии
    - Использует единый кэш, разделяемый между всеми сигналами
    - Может работать без полного SignalContext
    """

    THRESHOLDS = [100_000, 250_000, 500_000, 1_000_000]
    LOOKBACK_MINUTES = 5

    async def check(self, ctx: SignalContext) -> dict | None:
        # ── Новый путь: FeatureEngine ──
        if ctx.features is not None:
            return await self._check_via_features(ctx)

        # ── Fallback: старый путь (для обратной совместимости) ──
        return self._check_via_context(ctx)

    async def _check_via_features(self, ctx: SignalContext) -> dict | None:
        """Проверка через FeatureEngine."""
        trades = await ctx.features.get_feature(ctx.symbol, "whale.trades")
        if not trades:
            return None

        cutoff = time.time() - self.LOOKBACK_MINUTES * 60
        recent = [t for t in trades if t.get("ts", 0) > cutoff]

        if not recent:
            return None

        best_trade = max(recent, key=lambda t: t.get("notional", 0))
        notional = best_trade.get("notional", 0)
        side = best_trade.get("side", "buy")

        # Score по порогу
        score = 40
        for i, threshold in enumerate(self.THRESHOLDS):
            if notional > threshold:
                score = 60 + (i + 1) * 10

        # Дополнительные фичи из FeatureEngine
        cvd = await ctx.features.get_feature(ctx.symbol, "whale.cvd")
        aggression = await ctx.features.get_feature(ctx.symbol, "whale.aggression")

        meta = {
            "value": round(notional, 2),
            "price": round(best_trade.get("price", 0), 2),
            "size": round(best_trade.get("size", 4), 4),
            "side": side,
            "exchange": best_trade.get("exchange", ""),
            "type": best_trade.get("type", "market"),
            "cvd": round(cvd, 2) if cvd else None,
            "aggression_5m": (aggression or {}).get("5m"),
        }

        return self._result(
            symbol=ctx.symbol,
            score=min(100, score),
            direction="buy" if side == "buy" else "sell",
            meta=meta,
        )

    def _check_via_context(self, ctx: SignalContext) -> dict | None:
        """Старая логика — для обратной совместимости."""
        if not ctx.whale_trades:
            return None

        cutoff = time.time() - self.LOOKBACK_MINUTES * 60
        best_trade = None

        for t in ctx.whale_trades:
            if t.get("ts", 0) > cutoff:
                notional = t.get("notional", 0)
                if best_trade is None or notional > best_trade.get("notional", 0):
                    best_trade = t

        if best_trade is None:
            return None

        notional = best_trade["notional"]
        score = 40
        for i, threshold in enumerate(self.THRESHOLDS):
            if notional > threshold:
                score = 60 + (i + 1) * 10

        side = best_trade.get("side", "buy")

        return self._result(
            symbol=ctx.symbol,
            score=min(100, score),
            direction="buy" if side == "buy" else "sell",
            meta={
                "value": round(notional, 2),
                "price": round(best_trade.get("price", 0), 2),
                "size": round(best_trade.get("size", 4), 4),
                "side": side,
                "exchange": best_trade.get("exchange", ""),
                "type": best_trade.get("type", "market"),
            },
        )
