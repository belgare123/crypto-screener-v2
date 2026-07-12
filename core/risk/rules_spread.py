"""
SpreadRule — блокирует/редуцирует сигналы на основе spread vs ATR.

Логика:
- spread_bps < 0.5 * atr_14_pct_in_bps → ALLOW (нормальный спред)
- spread_bps > 0.8 * atr_14_pct_in_bps → REDUCE (широкий)
- spread_bps > atr_14_pct_in_bps → BLOCK (аномальный)

Все пороги берутся из current market state, а не фиксированные.
"""
from __future__ import annotations

from core.risk.models import RiskContext, RiskReason, RiskVerdict
from core.risk.rules import RiskRule


class SpreadRule(RiskRule):
    name = "spread"

    # Пороги от ATR
    block_ratio: float = 1.0    # spread > 100% ATR → BLOCK
    reduce_ratio: float = 0.8   # spread > 80% ATR → REDUCE

    def __init__(self, block_ratio: float = 1.0, reduce_ratio: float = 0.8):
        self.block_ratio = block_ratio
        self.reduce_ratio = reduce_ratio

    async def evaluate(self, ctx: RiskContext) -> RiskReason:
        if ctx.spread_bps <= 0 or ctx.atr_14_pct <= 0:
            return RiskReason(self.name, RiskVerdict.ALLOW, "no data")

        # ATR в bps = atr_14_pct * 100
        atr_in_bps = ctx.atr_14_pct * 100
        if atr_in_bps <= 0:
            return RiskReason(self.name, RiskVerdict.ALLOW, "atr is zero")

        spread_to_atr = ctx.spread_bps / atr_in_bps

        if spread_to_atr > self.block_ratio:
            return RiskReason(
                self.name, RiskVerdict.BLOCK,
                f"spread={ctx.spread_bps:.1f}bps exceeds ATR ({atr_in_bps:.1f}bps)",
                severity=min(spread_to_atr / 2, 1.0),
                extra={"spread_bps": ctx.spread_bps, "atr_bps": atr_in_bps, "ratio": spread_to_atr},
            )

        if spread_to_atr > self.reduce_ratio:
            return RiskReason(
                self.name, RiskVerdict.REDUCE,
                f"spread={ctx.spread_bps:.1f}bps is {spread_to_atr:.1%} of ATR ({atr_in_bps:.1f}bps)",
                severity=0.3,
                extra={"spread_bps": ctx.spread_bps, "ratio": spread_to_atr},
            )

        return RiskReason(self.name, RiskVerdict.ALLOW, f"spread/ATR={spread_to_atr:.2f}")
