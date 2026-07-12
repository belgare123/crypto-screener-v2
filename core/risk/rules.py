"""
RiskRule — базовый интерфейс для всех правил Risk Engine.
"""
from __future__ import annotations

import abc

from core.risk.models import RiskContext, RiskReason


class RiskRule(abc.ABC):
    """Одно правило Risk Engine.

    `name` — уникальное имя для логирования и метрик.
    `evaluate(ctx)` → RiskReason (ALLOW / BLOCK / REDUCE)
    """

    name: str = ""

    @abc.abstractmethod
    async def evaluate(self, ctx: RiskContext) -> RiskReason:
        ...
