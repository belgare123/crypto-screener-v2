"""
Explainable AI — человеко-читаемые объяснения сигналов.

Генерируется ExplainableAIEngine (core/explainable_ai.py).
Форматирует сигнал с объяснением вместо голого Score.
"""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core import SignalResult


@register(
    "signal_explain",
    description="Объяснение сигнала: почему сработал и что значит",
    category="analytics",
    default_score=30,
    cooldown=3600,
)
class ExplainableAISignal(BaseSignal):
    """Explainable AI."""

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        return None
