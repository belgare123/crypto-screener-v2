"""
WeightUpdater — динамическое обновление весов стратегий на основе winrate.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from core.learning.models import WinRateEntry, StrategyStats
from core.learning.winrate import WinRateTracker

logger = logging.getLogger(__name__)


class WeightUpdater:
    """Пересчитывает target_weight для стратегии.

    Формула:
        target_weight = base_weight × (winrate / target_winrate)

    Где:
    - winrate = adjusted_winrate (wald-adj для малых n)
    - target_winrate = желаемый winrate (0.55 = 55%)
    - base_weight = вес, если winrate == target_winrate

    Ограничения:
    - min_weight = 0.05 (стратегия не уходит в ноль)
    - max_weight = 0.50 (одна стратегия не доминирует)
    - при n < min_trades → вес не меняется
    - weight smoothing: new = old × (1 - alpha) + target × alpha
    """

    def __init__(self, tracker: WinRateTracker,
                 target_winrate: float = 0.55,
                 base_weight: float = 0.20,
                 min_weight: float = 0.05,
                 max_weight: float = 0.50,
                 alpha: float = 0.3,
                 weight_update_fn: Callable | None = None):
        """
        Args:
            tracker: WinRateTracker с данными
            target_winrate: желаемый winrate (0.55 = 55%)
            base_weight: вес при winrate == target_winrate
            min_weight: минимальный вес
            max_weight: максимальный вес
            alpha: smoothing factor (0.3 = 30% нового веса)
            weight_update_fn: callback при изменении веса (strategy, new_weight)
        """
        self.tracker = tracker
        self.target_winrate = target_winrate
        self.base_weight = base_weight
        self.min_weight = min_weight
        self.max_weight = max_weight
        self.alpha = alpha
        self.weight_update_fn = weight_update_fn or (lambda name, w: None)

        # Текущие веса стратегий (сглаженные)
        self._current_weights: dict[str, float] = {}

    def compute_target(self, stats: StrategyStats) -> float:
        """Рассчитать target_weight для стратегии."""
        if stats.total_trades < self.tracker.min_trades:
            # Недостаточно данных — возвращаем текущий вес или base
            return self._current_weights.get(stats.strategy_name, self.base_weight)

        winrate = stats.adjusted_winrate
        target = self.base_weight * (winrate / self.target_winrate)

        # Ограничения
        target = max(self.min_weight, min(self.max_weight, target))
        return round(target, 4)

    def update_weight(self, strategy_name: str,
                      current_weight: float | None = None) -> float:
        """Обновить вес одной стратегии (smoothed).

        Args:
            strategy_name: имя стратегии
            current_weight: текущий вес (если None — берётся из _current_weights)

        Returns:
            новый вес
        """
        stats = self.tracker.strategy_stats(strategy_name)
        if stats is None:
            return self._current_weights.get(strategy_name, self.base_weight)

        target = self.compute_target(stats)
        old = current_weight if current_weight is not None else \
              self._current_weights.get(strategy_name, self.base_weight)

        # Smoothing
        new_weight = old * (1 - self.alpha) + target * self.alpha
        new_weight = round(max(self.min_weight, min(self.max_weight, new_weight)), 4)

        self._current_weights[strategy_name] = new_weight

        if abs(new_weight - old) > 0.001:
            logger.info("[weight] %s: %.4f → %.4f (target=%.4f, wr=%.2f%%, n=%d)",
                       strategy_name, old, new_weight, target,
                       (stats.winrate or 0) * 100, stats.total_trades)
            self.weight_update_fn(strategy_name, new_weight)

        return new_weight

    def update_all(self) -> dict[str, float]:
        """Обновить веса всех стратегий с данными.

        Returns:
            dict[strategy_name → new_weight]
        """
        results = {}
        for name in self.tracker.strategies():
            results[name] = self.update_weight(name)
        return results

    def get_weight(self, strategy_name: str) -> float:
        """Текущий вес стратегии."""
        return self._current_weights.get(strategy_name, self.base_weight)

    def set_weight(self, strategy_name: str, weight: float):
        """Принудительно установить вес (инициализация)."""
        weight = max(self.min_weight, min(self.max_weight, weight))
        self._current_weights[strategy_name] = round(weight, 4)

    def reset_weights(self, default: float | None = None):
        """Сброс всех весов на default или base_weight."""
        default = default or self.base_weight
        for name in list(self._current_weights.keys()):
            self._current_weights[name] = default
