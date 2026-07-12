"""
Risk Engine — пре-трейд фильтрация сигналов.

Каждый сигнал перед отправкой проходит через RiskEngine:
- Проверяет spread, ATR, liquidity, session
- Возвращает ALLOW / BLOCK / REDUCE (с причиной)
- Работает в shadow-mode для сбора статистики
"""
from __future__ import annotations

from .models import RiskVerdict, RiskReason, RiskResult, RiskContext
from .rules import RiskRule
from .rules_spread import SpreadRule
from .rules_atr import ATRRule
from .rules_liquidity import LiquidityRule
from .rules_session import SessionRule
from .engine import RiskEngine, get_risk_engine, reset_risk_engine

__all__ = [
    "RiskVerdict", "RiskReason", "RiskResult", "RiskContext",
    "RiskRule",
    "SpreadRule", "ATRRule", "LiquidityRule", "SessionRule",
    "RiskEngine", "get_risk_engine", "reset_risk_engine",
]
