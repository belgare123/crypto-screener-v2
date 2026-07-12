"""
Heatmap — сигналы тепловой карты рынка.

Генерируются HeatmapEngine (core/heatmap.py) — не event-driven.
Здесь только регистрация в реестре для отображения и статистики.
"""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core import SignalResult


@register(
    "heatmap",
    description="Тепловая карта: ТОП по объёму, моментуму, ликвидациям",
    category="heatmap",
    default_score=50,
    cooldown=1800,
)
class HeatmapSignal(BaseSignal):
    """
    Сигнал тепловой карты рынка.
    Генерируется HeatmapEngine, не через событийный check().
    """

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        return None
