"""
Learning Engine — трекинг winrate и динамическое обновление весов стратегий.

Компоненты:
- WinRateTracker — считает winrate per strategy, per regime, per exchange
- WeightUpdater — пересчитывает вес стратегии на основе historical performance
- LearningEngine — фасад, обновляет ConsensusEngine при достижении порога

Реализует:
- WinRate = wins / (wins + losses)
- Weight = base_weight × (winrate / target_winrate)
- PortfolioSharpe = avg_pnl / std_pnl (если сделок > min_trades)
"""
from __future__ import annotations

from .models import WinRateEntry, StrategyStats, RegimeStats
from .winrate import WinRateTracker
from .weight_updater import WeightUpdater
from .engine import LearningEngine, get_learning_engine, reset_learning_engine

__all__ = [
    "WinRateEntry", "StrategyStats", "RegimeStats",
    "WinRateTracker",
    "WeightUpdater",
    "LearningEngine", "get_learning_engine", "reset_learning_engine",
]
