"""
Signal Lifecycle — стадии Born → Growing → Confirmed → Weakening → Dead.

Генерируется SignalLifecycleEngine (core/signal_lifecycle.py).
"""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core import SignalResult


@register(
    "signal_lifecycle",
    description="Стадия жизненного цикла сигнала: Born/Growing/Confirmed/Weakening/Dead",
    category="ml",
    default_score=40,
    cooldown=600,
)
class SignalLifecycleSignal(BaseSignal):
    """Сигнал смены стадии жизненного цикла."""

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        return None


@register(
    "confidence_drift",
    description="⚠ Уверенность падает: Score X → Y за N минут",
    category="ml",
    default_score=60,
    cooldown=3600,
)
class ConfidenceDriftSignal(BaseSignal):
    """Сигнал падения уверенности."""

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        return None
