"""Liquidation Signal — объём ликвидаций как индикатор стресса/разворота."""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register


@register(
    name="liquidation",
    description="Аномальный объём ликвидаций: >$5M / 15мин = 70+",
    category="liquidation",
    default_score=60,
    cooldown=1800,
)
class LiquidationSignal(BaseSignal):
    """
    Смотрит объём ликвидаций за последние 15 минут.
    - >$5M → score 80 (сильный стресс)
    - >$2M → score 65 (умеренный)
    - >$500K → score 50 (лёгкий)
    - Преобладание sell-ликвидаций = потенциальное дно (long squeeze → buy)
    """

    HIGH = 5_000_000
    MED = 2_000_000
    LOW = 500_000

    async def check(self, ctx: SignalContext) -> dict | None:
        liqs = ctx.liquidations
        if not liqs or len(liqs) < 3:
            return None

        total = sum(l.get("notional", 0) for l in liqs)
        if total < self.LOW:
            return None

        sell_vol = sum(l.get("notional", 0) for l in liqs if l.get("side") == "sell")
        buy_vol = sum(l.get("notional", 0) for l in liqs if l.get("side") == "buy")
        sell_ratio = sell_vol / total if total > 0 else 0.5

        if total > self.HIGH:
            score = 80
        elif total > self.MED:
            score = 65
        else:
            score = 50

        # Sell-heavy = long squeeze → бычий сигнал
        if sell_ratio > 0.7:
            direction = "buy"
            score = min(100, score + 10)
        elif buy_vol / total > 0.7:
            direction = "sell"
            score = min(100, score + 10)
        else:
            direction = "neutral"

        return self._result(
            symbol=ctx.symbol,
            score=score,
            direction=direction,
            meta={
                "total_notional": round(total, 2),
                "sell_volume": round(sell_vol, 2),
                "buy_volume": round(buy_vol, 2),
                "sell_ratio": round(sell_ratio, 2),
                "count": len(liqs),
            },
        )
