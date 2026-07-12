"""
Market Replay — запись исторических снэпшотов рынка.

Генерируется MarketReplayEngine (core/market_replay.py).
"""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core import SignalResult


@register(
    "market_replay",
    description="📼 Исторический снэпшот: цена, стакан, CVD, сигналы",
    category="analytics",
    default_score=30,
    cooldown=7200,
)
class MarketReplaySignal(BaseSignal):
    """Market Replay."""

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        return None
