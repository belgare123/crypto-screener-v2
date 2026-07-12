"""
Market Analysis signals — Expected Move, Risk Meter, Noise Filter.

Генерируется MarketAnalysisEngine (core/market_analysis.py).
"""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core import SignalResult


@register(
    "expected_move",
    description="Ожидаемое движение цены ±X% (1σ, 68% confidence)",
    category="analytics",
    default_score=50,
    cooldown=3600,
)
class ExpectedMoveSignal(BaseSignal):
    """Expected Move."""

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        return None


@register(
    "risk_meter",
    description="Риск LOW/MEDIUM/HIGH на основе ATR + ликвидность + спред",
    category="analytics",
    default_score=40,
    cooldown=7200,
)
class RiskMeterSignal(BaseSignal):
    """Risk Meter."""

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        return None


@register(
    "noise_filter",
    description="⚠ Рынок пилит — Noise X% (Skip)",
    category="analytics",
    default_score=30,
    cooldown=7200,
)
class NoiseFilterSignal(BaseSignal):
    """Noise Filter."""

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        return None
