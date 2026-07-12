"""
OpportunityRanking — накопление сигналов за окно + ранжирование лучших.

Каждый тик: добавляет ConsensusResult в окно символа.
Раз в N минут: выбирает топ-K возможностей по score.
Устаревшие окна (max_age) удаляются.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict

from core.consensus.models import ConsensusResult, OpportunityWindow

logger = logging.getLogger(__name__)


class OpportunityRanking:
    """Ранжирование возможностей по score за временное окно."""

    def __init__(self, window_minutes: int = 10, max_age_seconds: int = 600,
                 top_k: int = 3):
        self.window_seconds = window_minutes * 60
        self.max_age = max_age_seconds
        self.top_k = top_k
        self._windows: dict[str, OpportunityWindow] = {}

    def add_result(self, result: ConsensusResult):
        """Добавить ConsensusResult в окно символа."""
        now = time.time()
        if result.symbol not in self._windows:
            self._windows[result.symbol] = OpportunityWindow(
                symbol=result.symbol,
                first_seen=now,
            )
        w = self._windows[result.symbol]
        w.entries.append(result)
        w.last_seen = now
        w.total_signals += 1
        w.best_score = max(w.best_score, result.score)
        # Истечение по времени
        w.expired = (now - w.first_seen) > self.max_age

    def get_top(self, k: int | None = None) -> list[OpportunityWindow]:
        """Вернуть топ-K активных возможностей по best_score."""
        k = k or self.top_k
        self._purge()

        valid = [w for w in self._windows.values() if not w.expired and w.best_score > 0]
        valid.sort(key=lambda w: w.best_score, reverse=True)
        return valid[:k]

    def _purge(self):
        """Удалить устаревшие окна."""
        now = time.time()
        expired_keys = [
            sym for sym, w in self._windows.items()
            if (now - w.last_seen) > self.max_age
        ]
        for sym in expired_keys:
            del self._windows[sym]

    @property
    def active_count(self) -> int:
        self._purge()
        return len([w for w in self._windows.values() if not w.expired])

    def clear(self):
        self._windows.clear()
