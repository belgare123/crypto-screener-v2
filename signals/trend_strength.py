"""
Trend Strength — сигналы силы тренда (EMA, Slope, HH/HL).

Генерируются TrendStrengthEngine (core/trend_strength.py).
"""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core import SignalResult


@register(
    "trend_strength",
    description="Сила тренда: strong_up/weak_up/sideways/weak_down/strong_down",
    category="trend",
    default_score=60,
    cooldown=3600,
)
class TrendStrengthSignal(BaseSignal):
    """
    Сигнал силы тренда.
    Генерируется TrendStrengthEngine, не через событийный check().
    """

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        return None
