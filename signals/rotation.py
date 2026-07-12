"""
Rotation — сигналы ротации капитала между секторами.

Генерируются RotationDetector (core/rotation.py) — не event-driven.
Здесь только регистрация в реестре.
"""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core import SignalResult


@register(
    "rotation",
    description="Ротация капитала между секторами (Risk On/Off)",
    category="rotation",
    default_score=60,
    cooldown=3600,
)
class RotationSignal(BaseSignal):
    """
    Сигнал ротации капитала между секторами.
    Генерируется RotationDetector, не через событийный check().
    """

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        return None
