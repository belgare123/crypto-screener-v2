"""
Signal DNA — регистрация отпечатка сигнала.

DNA собирается автоматически для каждого сигнала через DNACollector.
Не генерирует событийные проверки.
"""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core import SignalResult


@register(
    "signal_dna",
    description="Отпечаток сигнала: Volume, OI, Delta, Walls, Momentum",
    category="ml",
    default_score=0,
    cooldown=0,
)
class SignalDNASignal(BaseSignal):
    """
    DNA — пассивный сборщик данных.
    Не генерирует сигналов через check().
    """

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        return None
