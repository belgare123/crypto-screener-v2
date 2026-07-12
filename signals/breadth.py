"""
Market Breadth — сигналы ширины рынка.

Генерируются MarketBreadthEngine (core/market_breadth.py) — не event-driven.
Здесь только регистрация в реестре для отображения и статистики.
"""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core import SignalResult


@register(
    "market_breadth",
    description="Ширина рынка: % зелёных монет, экстремальные режимы",
    category="market_breadth",
    default_score=65,
    cooldown=1800,
)
class MarketBreadthSignal(BaseSignal):
    """
    Сигнал ширины рынка.
    Срабатывает когда подавляющее большинство монет зелёные или красные.
    Генерируется MarketBreadthEngine, не через событийный check().
    """

    async def check(self, ctx: SignalContext) -> SignalResult | None:
        # Этот метод не вызывается — сигнал генерируется MarketBreadthEngine.tick()
        return None
