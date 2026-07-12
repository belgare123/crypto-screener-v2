"""
LearningEngine — фасад для трекинга winrate и обновления весов.
"""
from __future__ import annotations

import logging
import time

from core.learning.models import WinRateEntry
from core.learning.winrate import WinRateTracker
from core.learning.weight_updater import WeightUpdater

logger = logging.getLogger(__name__)


class LearningEngine:
    """Фасад Learning Engine.

    Объединяет WinRateTracker и WeightUpdater.
    Может быть подключён к ConsensusEngine для динамических весов.
    """

    def __init__(self, tracker: WinRateTracker | None = None,
                 updater: WeightUpdater | None = None,
                 shadow: bool = True,
                 update_interval: float = 3600.0):  # проверка весов раз в час
        self.shadow = shadow
        self.update_interval = update_interval
        self.tracker = tracker or WinRateTracker(min_trades=10)
        self.updater = updater or WeightUpdater(tracker=self.tracker)
        self._last_update = 0.0

    # ── Record trades ──

    def record_result(self, strategy_name: str, symbol: str, side: str,
                       entry_price: float, exit_price: float, pnl: float,
                       regime: str = "unknown", exchange: str = "bybit",
                       **extra) -> WinRateEntry:
        """Записать результат одной сделки.

        Автоматически проверяет, нужно ли обновить веса.
        """
        pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price else 0
        if side == "sell":
            pnl_pct = -pnl_pct

        entry = WinRateEntry(
            strategy_name=strategy_name,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=pnl_pct,
            entry_time=extra.pop("entry_time", time.time()),
            exit_time=time.time(),
            regime=regime,
            exchange=exchange,
            extra=extra,
        )

        self.tracker.record(entry)

        # Проверка необходимости обновить веса
        if not self.shadow:
            now = time.time()
            if now - self._last_update > self.update_interval:
                self._maybe_update_weights()
                self._last_update = now

        return entry

    def record_batch(self, entries: list[dict]) -> list[WinRateEntry]:
        """Записать группу сделок (словари с ключами как у record_result)."""
        results = []
        for e in entries:
            results.append(self.record_result(**e))
        return results

    # ── Weights ──

    def _maybe_update_weights(self):
        """Проверить и обновить веса (лог в shadow-mode)."""
        updates = self.updater.update_all()
        if updates:
            logger.info("[learning] Weight updates: %s", updates)
            if self.shadow:
                logger.info("[learning] SHADOW — weights not applied to engine")

    def update_weights(self) -> dict[str, float]:
        """Принудительно обновить все веса (вне interval)."""
        updates = self.updater.update_all()
        return updates

    def get_weight(self, strategy_name: str) -> float:
        return self.updater.get_weight(strategy_name)

    def set_weight(self, strategy_name: str, weight: float):
        self.updater.set_weight(strategy_name, weight)

    # ── Stats ──

    def strategy_stats(self, name: str):
        return self.tracker.strategy_stats(name)

    def winrate_table(self) -> list[dict]:
        return self.tracker.winrate_table()

    def top_strategies(self, limit: int = 5):
        return self.tracker.top_strategies(limit)

    @property
    def total_trades(self) -> int:
        return self.tracker.total_records


# Singleton
_learning_engine: LearningEngine | None = None


def get_learning_engine(tracker: WinRateTracker | None = None,
                         updater: WeightUpdater | None = None,
                         shadow: bool = True) -> LearningEngine:
    global _learning_engine
    if _learning_engine is None:
        _learning_engine = LearningEngine(
            tracker=tracker, updater=updater, shadow=shadow,
        )
    return _learning_engine


def reset_learning_engine():
    global _learning_engine
    _learning_engine = None
