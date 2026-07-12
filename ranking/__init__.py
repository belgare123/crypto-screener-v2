"""Ranking — оценка и взвешивание сигналов."""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any


class SignalScorer:
    """
    Оценка силы сигнала на основе истории.
    - Weighted average: недавние сигналы важнее
    - Confidence: сколько раз подтверждался
    - Symbol momentum: если монета в топе сигналов — выше вес
    """

    def __init__(self, history_size: int = 100):
        self._history: list[dict] = []
        self._maxlen = history_size

    def add_result(self, signal_name: str, symbol: str, score: float, direction: str):
        self._history.append({
            "signal": signal_name,
            "symbol": symbol,
            "score": score,
            "direction": direction,
        })
        if len(self._history) > self._maxlen:
            self._history = self._history[-self._maxlen:]

    def symbol_momentum(self, symbol: str, window: int = 10) -> float:
        """
        Моментум монеты: средний score за последние N сигналов.
        Выше 70 = горячая монета.
        """
        relevant = [
            h for h in self._history[-window:]
            if h["symbol"] == symbol
        ]
        if not relevant:
            return 0.0
        return statistics.mean(h["score"] for h in relevant)

    def cluster_score(self, symbol: str, time_window: float = 300) -> float:
        """
        Кластерный score: если за 5 мин пришло 3+ сигнала — бонус.
        """
        # simplified: count signals in last N entries
        recent = [h for h in self._history[-50:] if h["symbol"] == symbol]
        count = len(recent)
        if count >= 5:
            return min(30, count * 5)
        return 0.0

    def probability(self, symbol: str) -> float:
        """
        Вероятность успеха на основе истории.
        Пока заглушка — будет наполняться по мере сбора статистики.
        """
        momentum = self.symbol_momentum(symbol)
        cluster = self.cluster_score(symbol)
        return min(99.0, 50 + momentum * 0.3 + cluster * 0.2)
