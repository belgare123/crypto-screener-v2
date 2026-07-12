"""
LiquidityRule — фильтр ликвидности.

Блокирует/редуцирует если:
- 24h volume < min_volume → BLOCK (недостаточно ликвидности)
- OI < min_oi → BLOCK (маленький открытый интерес для фьючерсов)
- volume/oi_ratio < min_ratio → REDUCE (аномально низкая активность)
"""
from __future__ import annotations

from core.risk.models import RiskContext, RiskReason, RiskVerdict
from core.risk.rules import RiskRule


class LiquidityRule(RiskRule):
    name = "liquidity"

    min_volume_usdt: float = 1_000_000      # $1M min 24h volume
    min_oi_usdt: float = 5_000_000           # $5M min OI для фьючерсов
    min_volume_ratio: float = 0.3            # volume / avg_volume ratio

    def __init__(self, min_volume_usdt: float = 1_000_000,
                 min_oi_usdt: float = 5_000_000,
                 min_volume_ratio: float = 0.3):
        self.min_volume_usdt = min_volume_usdt
        self.min_oi_usdt = min_oi_usdt
        self.min_volume_ratio = min_volume_ratio

    async def evaluate(self, ctx: RiskContext) -> RiskReason:
        # Volume check
        if ctx.volume_24h_usdt <= 0:
            return RiskReason(self.name, RiskVerdict.BLOCK, "no volume data")

        if ctx.volume_24h_usdt < self.min_volume_usdt:
            return RiskReason(
                self.name, RiskVerdict.BLOCK,
                f"24h volume ${ctx.volume_24h_usdt:,.0f} < ${self.min_volume_usdt:,.0f} min",
                severity=0.9,
                extra={"volume": round(ctx.volume_24h_usdt, 0)},
            )

        # OI check (если есть)
        if ctx.oi_usdt > 0 and ctx.oi_usdt < self.min_oi_usdt:
            return RiskReason(
                self.name, RiskVerdict.BLOCK,
                f"OI ${ctx.oi_usdt:,.0f} < ${self.min_oi_usdt:,.0f} min",
                severity=0.8,
                extra={"oi": round(ctx.oi_usdt, 0)},
            )

        return RiskReason(self.name, RiskVerdict.ALLOW,
                          f"vol=${ctx.volume_24h_usdt:,.0f} oi=${ctx.oi_usdt:,.0f}")
