"""
AI Clustering — сигнал ML кластеризации.

Генерируется ClusteringEngine (core/ai_clustering.py).
"""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core import SignalResult


@register(
    "ai_cluster",
    description="ML кластеризация: сигнал попал в кластер с win_rate > 70%",
    category="ml",
    default_score=70,
    cooldown=3600,
)
class AIClusterSignal(BaseSignal):
    """
    Сигнал AI кластеризации.
    """

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        return None
