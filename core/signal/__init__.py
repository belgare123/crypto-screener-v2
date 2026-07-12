"""
Signal Engine — интеграция: Consensus → Risk → OME → Cooldown → Telegram.

Pipeline:
1. Принимает ConsensusResult (от ConsensusEngine)
2. Прогоняет через RiskEngine (уже shadow)
3. Если ALLOW → PositionSizer → OME.execute_signal()
4. Создаёт SignalResult
5. Проверяет cooldown (антиспам)
6. Отправляет через TelegramNotifier
7. Логирует метрики

Всё в shadow-mode до полной верификации.
"""
from __future__ import annotations

from .engine import SignalEngine, get_signal_engine, reset_signal_engine

__all__ = [
    "SignalEngine", "get_signal_engine", "reset_signal_engine",
]
