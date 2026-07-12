"""Smart Money Signal — конфлюенция факторов: CVD + Volume + Whale + Liquidation + Orderbook."""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register


@register(
    name="smart_money",
    description="Смарт-мани: CVD + Volume + Whale + Liquidation + Orderbook",
    category="smart_money",
    default_score=90,
    cooldown=3600,
)
class SmartMoneySignal(BaseSignal):
    """
    Smart Money Score — комбинированный сигнал.
    Смотрит одновременно на CVD, Volume, Large Orders, Liquidations, Orderbook.
    Если 3+ фактора совпали — score 70+.
    """

    async def check(self, ctx: SignalContext) -> dict | None:
        factors = []
        total_score = 0.0

        # 1. CVD (Cumulative Volume Delta)
        if ctx.cvd != 0:
            cvd_abs = abs(ctx.cvd)
            if cvd_abs > 1_000_000:
                factors.append(("High CVD", 20, round(ctx.cvd, 2)))
                total_score += 20
            elif cvd_abs > 500_000:
                factors.append(("Moderate CVD", 10, round(ctx.cvd, 2)))
                total_score += 10

        # 2. Volume spike из ticker
        if ctx.ticker:
            high_24h = ctx.ticker.get("high_24h", 0)
            low_24h = ctx.ticker.get("low_24h", 0)
            if high_24h and low_24h:
                range_val = (high_24h - low_24h) / low_24h * 100
                if range_val > 5:
                    factors.append(("Wide Range (high vol)", 10, round(range_val, 1)))
                    total_score += 10

        # 3. Whale trade
        if ctx.whale_trades:
            recent_count = len([t for t in ctx.whale_trades if t.get("notional", 0) > 100_000])
            if recent_count >= 3:
                factors.append(("Whale cluster", 25, recent_count))
                total_score += 25
            elif recent_count >= 1:
                factors.append(("Whale detected", 15, recent_count))
                total_score += 15

        # 4. Ликвидации (стресс / разворот)
        if ctx.liquidations and len(ctx.liquidations) >= 3:
            liq_total = sum(l.get("notional", 0) for l in ctx.liquidations)
            if liq_total > 2_000_000:
                factors.append(("High liq volume", 20, round(liq_total, 2)))
                total_score += 20
            elif liq_total > 500_000:
                factors.append(("Moderate liq volume", 10, round(liq_total, 2)))
                total_score += 10

        # 5. Orderbook — дисбаланс
        ob = ctx.orderbook
        if ob is not None:
            imbalance = ob.imbalance_ratio
            if imbalance == float("inf"):
                imbalance = 10.0
            if imbalance > 3.0:
                factors.append(("Bid wall imbalance", 15, round(imbalance, 1)))
                total_score += 15
            elif imbalance < 0.33:
                factors.append(("Ask wall imbalance", 15, round(imbalance, 1)))
                total_score += 15

        if len(factors) < 2:
            return None

        # Направление
        buy_signals = sum(f[1] for f in factors if f[1] > 0)
        sell_signals = abs(sum(f[1] for f in factors if f[1] < 0))
        direction = "buy" if buy_signals > sell_signals else "sell"

        score = min(100, max(0, total_score + 40))  # база 40

        return self._result(
            symbol=ctx.symbol,
            score=score,
            direction=direction,
            meta={
                "factors": factors,
                "total_score": total_score,
                "factor_count": len(factors),
            },
        )
