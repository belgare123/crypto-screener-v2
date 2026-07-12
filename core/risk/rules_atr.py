"""
ATRRule — фильтр волатильности через ATR.

Когда ATR аномально высокий (внезапный всплеск) — REDUCE или BLOCK.
Когда ATR слишком низкий (штиль, могут быть ложные сигналы) — REDUCE.

Пороги:
- atr_14_pct > high_atr_pct → BLOCK (экстремальная волатильность)
- atr_14_pct < low_atr_pct → REDUCE (штиль, ложные пробои)
- atr_change_1h > surge_threshold → REDUCE (внезапный всплеск волатильности)
"""
from __future__ import annotations

from core.risk.models import RiskContext, RiskReason, RiskVerdict
from core.risk.rules import RiskRule


class ATRRule(RiskRule):
    name = "atr"

    high_atr_pct: float = 5.0    # > 5% ATR(14) от цены → BLOCK
    low_atr_pct: float = 0.2     # < 0.2% → REDUCE (штиль)
    surge_threshold: float = 3.0  # 300% рост ATR за час → REDUCE

    def __init__(self, high_atr_pct: float = 5.0, low_atr_pct: float = 0.2,
                 surge_threshold: float = 3.0):
        self.high_atr_pct = high_atr_pct
        self.low_atr_pct = low_atr_pct
        self.surge_threshold = surge_threshold

    async def evaluate(self, ctx: RiskContext) -> RiskReason:
        if ctx.atr_14_pct <= 0:
            return RiskReason(self.name, RiskVerdict.ALLOW, "no ATR data")

        # Экстремальная вола
        if ctx.atr_14_pct > self.high_atr_pct:
            return RiskReason(
                self.name, RiskVerdict.BLOCK,
                f"ATR={ctx.atr_14_pct:.2f}% exceeds {self.high_atr_pct}% high threshold",
                severity=0.9,
                extra={"atr_pct": round(ctx.atr_14_pct, 2)},
            )

        # Штиль
        if ctx.atr_14_pct < self.low_atr_pct:
            return RiskReason(
                self.name, RiskVerdict.REDUCE,
                f"ATR={ctx.atr_14_pct:.3f}% below {self.low_atr_pct}% low threshold (stale)",
                severity=0.4,
                extra={"atr_pct": round(ctx.atr_14_pct, 2)},
            )

        return RiskReason(self.name, RiskVerdict.ALLOW, f"ATR={ctx.atr_14_pct:.2f}%")
