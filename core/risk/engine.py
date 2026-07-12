"""
RiskEngine — сборщик правил, проверяет каждый сигнал перед отправкой.

Поток:
1. Получает RiskContext от вызывающего кода
2. Прогоняет через все зарегистрированные RiskRule
3. Собирает RiskResult (ALLOW / BLOCK / REDUCE)
4. Собирает метрики (blocked_by_reason, passed_ratio)

Shadow mode: логирует, но не блокирует реально.
Active mode: BLOCK возвращает блокировку вызывающему коду.
"""
from __future__ import annotations

import logging
from collections import Counter

from core.risk.models import RiskContext, RiskResult, RiskVerdict
from core.risk.rules import RiskRule

logger = logging.getLogger(__name__)


class RiskEngine:
    """Пре-трейд фильтрация сигналов."""

    def __init__(self, shadow: bool = True):
        self.shadow = shadow
        self._rules: list[RiskRule] = []
        self._stats: Counter = Counter()  # rule_name:blocked → count

    def add_rule(self, rule: RiskRule):
        """Добавить правило (в порядке приоритета — первое BLOCK побеждает)."""
        self._rules.append(rule)
        logger.info("[risk] Added rule: %s (%s)", rule.name, type(rule).__name__)

    def remove_rule(self, name: str):
        self._rules = [r for r in self._rules if r.name != name]

    async def evaluate(self, ctx: RiskContext) -> RiskResult:
        """Проверить сигнал по всем правилам."""
        result = RiskResult(symbol=ctx.symbol)

        for rule in self._rules:
            try:
                reason = await rule.evaluate(ctx)
                result.add(reason)
                # Логируем статистику
                if reason.verdict in (RiskVerdict.BLOCK, RiskVerdict.REDUCE):
                    self._stats[f"{rule.name}:{reason.verdict.value}"] += 1
            except Exception:
                logger.exception("[risk] Rule %s failed on %s", rule.name, ctx.symbol)
                continue

        if self.shadow:
            if result.verdict != RiskVerdict.ALLOW:
                logger.info(
                    "[risk:shadow] %s %s %s | reasons=%d",
                    ctx.symbol, result.verdict.value, result.summary,
                    len(result.reasons),
                )
            # In shadow mode, always return ALLOW (don't actually block)
            shadow_result = RiskResult(symbol=ctx.symbol)
            shadow_result.reasons = result.reasons
            return shadow_result

        return result

    def stats(self) -> dict:
        return dict(self._stats)

    def reset_stats(self):
        self._stats.clear()

    @property
    def rules(self) -> list[RiskRule]:
        return list(self._rules)


# Singleton
_risk_engine: RiskEngine | None = None


def get_risk_engine(shadow: bool = True) -> RiskEngine:
    """Получить (или создать) глобальный RiskEngine."""
    global _risk_engine
    if _risk_engine is None:
        _risk_engine = RiskEngine(shadow=shadow)
    return _risk_engine


def reset_risk_engine():
    """Сбросить синглтон (для тестов)."""
    global _risk_engine
    _risk_engine = None
