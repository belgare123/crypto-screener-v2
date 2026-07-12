"""
Learning Engine — модели данных.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WinRateEntry:
    """Одна запись результата сделки для обучения."""
    strategy_name: str
    symbol: str
    side: str                # buy | sell
    entry_price: float
    exit_price: float
    pnl: float               # realised PnL (USDT)
    pnl_pct: float           # % PnL
    entry_time: float
    exit_time: float
    regime: str = "unknown"  # regime на момент сделки
    exchange: str = "bybit"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def won(self) -> bool:
        """Позитивная сделка (PnL > 0)."""
        return self.pnl > 0

    @property
    def duration_hours(self) -> float:
        return (self.exit_time - self.entry_time) / 3600


@dataclass
class StrategyStats:
    """Статистика стратегии на N сделок."""
    strategy_name: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    max_drawdown: float = 0.0
    sharpe: float = 0.0
    avg_duration_hours: float = 0.0
    winrate: float = 0.0
    last_updated: float = 0.0

    def update(self, entry: WinRateEntry):
        """Добавить одну сделку в статистику."""
        self.total_trades += 1
        self.total_pnl += entry.pnl
        if entry.won:
            self.wins += 1
        else:
            self.losses += 1
        self.avg_pnl = self.total_pnl / self.total_trades
        self.winrate = self.wins / self.total_trades if self.total_trades > 0 else 0.0
        self.last_updated = entry.exit_time

    @property
    def is_significant(self) -> bool:
        """Минимально значимая выборка."""
        return self.total_trades >= 10

    @property
    def pf(self) -> float:
        """Profit Factor — gross profit / gross loss."""
        if self.losses == 0:
            return float("inf") if self.wins > 0 else 1.0
        return self.wins / self.losses if self.losses > 0 else 1.0

    @property
    def adjusted_winrate(self) -> float:
        """Winrate с поправкой на малое n (wald-adj)."""
        if self.total_trades == 0:
            return 0.0
        # Wald-adjusted: (wins + 2) / (n + 4) — аддитивное сглаживание
        return (self.wins + 2) / (self.total_trades + 4)


@dataclass
class RegimeStats:
    """Статистика стратегии в конкретном режиме (trending/ranging/volatile)."""
    regime: str
    strategy_stats: dict[str, StrategyStats] = field(default_factory=dict)
