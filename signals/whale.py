"""Whale Signal — крупные сделки (100K, 250K, 500K, 1M+)."""

from __future__ import annotations

import time

from signals.base import BaseSignal, SignalContext, register


@register(
    name="whale",
    description="Крупные сделки: $100K, $250K, $500K, $1M+",
    category="whale",
    default_score=80,
    cooldown=900,  # 15 min — whale сигналы важны быстро
    timeframes=["1m", "5m"],
)
class WhaleSignal(BaseSignal):
    """
    Следит за трейдами. Если за N минут есть сделка >= порога — сигнал.
    Score зависит от размера: $100K → 60, $250K → 75, $500K → 90, $1M → 100.
    """

    THRESHOLDS = [100_000, 250_000, 500_000, 1_000_000]
    LOOKBACK_MINUTES = 5

    async def check(self, ctx: SignalContext) -> dict | None:
        if not ctx.whale_trades:
            # Пробуем взять из контекста напрямую
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

        # Score по порогу
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
