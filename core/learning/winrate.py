"""
WinRateTracker — трекинг winrate по стратегиям и режимам.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict

from core.learning.models import WinRateEntry, StrategyStats, RegimeStats

logger = logging.getLogger(__name__)


class WinRateTracker:
    """Считает winrate per strategy + per regime.

    Хранит:
    - strategy_stats: dict[str, StrategyStats] — агрегат per strategy
    - regime_stats: dict[str, dict[str, StrategyStats]] — per regime → per strategy
    """

    def __init__(self, min_trades: int = 10, max_entries: int = 10000):
        """
        Args:
            min_trades: минимальное число сделок для значимой статистики
            max_entries: макс записей в истории (FIFO при превышении)
        """
        self.min_trades = min_trades
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: list[WinRateEntry] = []
        self._strategy_stats: dict[str, StrategyStats] = {}
        self._regime_stats: dict[str, dict[str, StrategyStats]] = defaultdict(dict)

    # ── Record ──

    def record(self, entry: WinRateEntry):
        """Записать результат одной сделки."""
        with self._lock:
            self._entries.append(entry)

            # Strategy stats
            if entry.strategy_name not in self._strategy_stats:
                self._strategy_stats[entry.strategy_name] = StrategyStats(
                    strategy_name=entry.strategy_name,
                )
            self._strategy_stats[entry.strategy_name].update(entry)

            # Regime × Strategy stats
            regime_strats = self._regime_stats.setdefault(entry.regime, {})
            if entry.strategy_name not in regime_strats:
                regime_strats[entry.strategy_name] = StrategyStats(
                    strategy_name=entry.strategy_name,
                )
            regime_strats[entry.strategy_name].update(entry)

            # FIFO purge
            if len(self._entries) > self.max_entries:
                self._entries = self._entries[-self.max_entries:]

            logger.debug("[winrate] %s %s %s pnl=%.2f won=%s",
                        entry.strategy_name, entry.symbol, entry.side,
                        entry.pnl, entry.won)

    def record_batch(self, entries: list[WinRateEntry]):
        """Записать группу результатов."""
        for e in entries:
            self.record(e)

    # ── Query ──

    def strategy_stats(self, strategy_name: str) -> StrategyStats | None:
        """Статистика конкретной стратегии."""
        with self._lock:
            return self._strategy_stats.get(strategy_name)

    def strategies(self) -> list[str]:
        """Имена всех трекаемых стратегий."""
        with self._lock:
            return list(self._strategy_stats.keys())

    def all_strategy_stats(self) -> dict[str, StrategyStats]:
        """Все стратегии с их статистикой."""
        with self._lock:
            return dict(self._strategy_stats)

    def regime_strategy_stats(self, regime: str, strategy: str) -> StrategyStats | None:
        """Статистика стратегии в конкретном режиме."""
        with self._lock:
            return self._regime_stats.get(regime, {}).get(strategy)

    def regimes(self) -> list[str]:
        """Все известные режимы."""
        with self._lock:
            return list(self._regime_stats.keys())

    @property
    def total_records(self) -> int:
        with self._lock:
            return len(self._entries)

    # ── Helpers ──

    def top_strategies(self, limit: int = 5) -> list[tuple[str, StrategyStats]]:
        """Топ стратегий по adjusted_winrate (с порогом min_trades)."""
        with self._lock:
            candidates = [
                (name, s) for name, s in self._strategy_stats.items()
                if s.total_trades >= self.min_trades
            ]
            candidates.sort(key=lambda x: x[1].adjusted_winrate, reverse=True)
            return candidates[:limit]

    def winrate_table(self) -> list[dict]:
        """Таблица для вывода в лог/дашборд."""
        with self._lock:
            rows = []
            for name, s in sorted(self._strategy_stats.items()):
                rows.append({
                    "strategy": name,
                    "trades": s.total_trades,
                    "winrate": round(s.winrate * 100, 1),
                    "adj_winrate": round(s.adjusted_winrate * 100, 1),
                    "pnl": round(s.total_pnl, 2),
                    "pf": round(s.pf, 2),
                })
            return rows
