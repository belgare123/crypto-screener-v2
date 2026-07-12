"""
Liquidity Zones — сигналы приближения к уровням поддержки/сопротивления.

Генерируются LiquidityZoneEngine (core/liquidity_zones.py).
"""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core import SignalResult


@register(
    "liquidity_zone",
    description="Приближение к зоне ликвидности (HL/Swing + Orderbook)",
    category="liquidity",
    default_score=50,
    cooldown=1800,
)
class LiquidityZoneSignal(BaseSignal):
    """
    Сигнал зоны ликвидности.
    Генерируется LiquidityZoneEngine, не через событийный check().
    """

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        return None
