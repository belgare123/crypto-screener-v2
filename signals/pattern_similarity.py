"""
Pattern Similarity — поиск похожих паттернов по DNA.

Генерируется PatternSimilarityEngine (core/pattern_similarity.py).
"""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core import SignalResult


@register(
    "pattern_similarity",
    description="Поиск похожих исторических ситуаций по Signal DNA",
    category="ml",
    default_score=50,
    cooldown=7200,
)
class PatternSimilaritySignal(BaseSignal):
    """
    Сигнал схожести паттернов.
    Генерируется PatternSimilarityEngine.
    """

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        return None
