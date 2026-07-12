"""Tests for Learning Engine — WinRateTracker, WeightUpdater, LearningEngine."""
from __future__ import annotations

import math

import pytest

from core.learning import (
    WinRateEntry, StrategyStats,
    WinRateTracker, WeightUpdater, LearningEngine,
    get_learning_engine, reset_learning_engine,
)


class TestWinRateEntry:
    def test_won(self):
        e = WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 21000, 100, 5.0, 1000, 2000)
        assert e.won is True

    def test_lost(self):
        e = WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 19000, -100, -5.0, 1000, 2000)
        assert e.won is False

    def test_duration_hours(self):
        e = WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 21000, 100, 5.0, 3600, 7200)
        assert math.isclose(e.duration_hours, 1.0, rel_tol=0.01)

    def test_regime_exchange(self):
        e = WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 21000, 100, 5.0,
                         1000, 2000, regime="trending", exchange="bybit")
        assert e.regime == "trending"
        assert e.exchange == "bybit"


class TestStrategyStats:
    def test_initial(self):
        s = StrategyStats("strat_a")
        assert s.total_trades == 0
        assert s.winrate == 0.0
        assert s.adjusted_winrate == 0.0  # 0 trades → 0.0

    def test_single_win(self):
        s = StrategyStats("strat_a")
        s.update(WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 21000, 100, 5.0, 1000, 2000))
        assert s.total_trades == 1
        assert s.wins == 1
        assert s.winrate == 1.0

    def test_multiple(self):
        s = StrategyStats("strat_a")
        s.update(WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 21000, 100, 5.0, 1000, 2000))
        s.update(WinRateEntry("strat_a", "ETH/USDT", "sell", 3000, 2900, 50, 3.3, 1000, 2000))
        s.update(WinRateEntry("strat_a", "BTC/USDT", "buy", 21000, 20500, -50, -2.4, 1000, 2000))
        assert s.total_trades == 3
        assert s.wins == 2
        assert s.winrate == 2/3

    def test_adjusted_winrate(self):
        s = StrategyStats("strat_a")
        s.update(WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 21000, 100, 5.0, 1000, 2000))
        # (1 + 2) / (1 + 4) = 3/5 = 0.6
        assert s.adjusted_winrate == 3.0 / 5.0

    def test_is_significant(self):
        s = StrategyStats("strat_a")
        for _ in range(5):
            s.update(WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 21000, 100, 5.0, 1000, 2000))
        assert s.is_significant is False  # < 10

    def test_pf(self):
        s = StrategyStats("strat_a")
        s.update(WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 21000, 200, 5.0, 1000, 2000))
        s.update(WinRateEntry("strat_a", "BTC/USDT", "sell", 20000, 19500, -50, -2.5, 1000, 2000))
        # pf should be > 1 when total profit > total loss
        assert s.pf > 0.0, f"pf={s.pf} should be positive"


class TestWinRateTracker:
    def test_record(self):
        t = WinRateTracker()
        t.record(WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 21000, 100, 5.0, 1000, 1800))
        assert t.total_records == 1

    def test_batch(self):
        t = WinRateTracker()
        entries = [
            WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 21000, 100, 5.0, 1000, 1800),
            WinRateEntry("strat_b", "ETH/USDT", "sell", 3000, 2800, 100, 6.7, 1000, 1800),
        ]
        t.record_batch(entries)
        assert t.total_records == 2

    def test_strategy_stats(self):
        t = WinRateTracker()
        t.record(WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 21000, 100, 5.0, 1000, 1800))
        s = t.strategy_stats("strat_a")
        assert s is not None
        assert s.wins == 1

    def test_nonexistent_strategy(self):
        t = WinRateTracker()
        assert t.strategy_stats("nope") is None

    def test_top_strategies(self):
        t = WinRateTracker(min_trades=2)
        for _ in range(2):
            t.record(WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 21000, 100, 5.0, 1000, 1800))
        t.record(WinRateEntry("strat_b", "ETH/USDT", "sell", 3000, 2900, 50, 3.3, 1000, 1800))
        top = t.top_strategies(limit=5)
        assert len(top) >= 1
        assert top[0][0] == "strat_a"

    def test_winrate_table(self):
        t = WinRateTracker()
        t.record(WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 21000, 100, 5.0, 1000, 1800))
        table = t.winrate_table()
        assert len(table) == 1
        assert table[0]["strategy"] == "strat_a"

    def test_strategies_list(self):
        t = WinRateTracker()
        t.record(WinRateEntry("strat_a", "BTC/USDT", "buy", 20000, 21000, 100, 5.0, 1000, 1800))
        t.record(WinRateEntry("strat_b", "ETH/USDT", "sell", 3000, 2900, 100, 6.7, 1000, 1800))
        assert set(t.strategies()) == {"strat_a", "strat_b"}


class TestWeightUpdater:
    def setup_method(self):
        self.tracker = WinRateTracker(min_trades=5)
        for _ in range(5):
            self.tracker.record(
                WinRateEntry("win_strat", "BTC/USDT", "buy", 20000, 21000, 100, 5.0, 1000, 1800)
            )

    def test_high_winrate_gets_higher_weight(self):
        wu = WeightUpdater(tracker=self.tracker, base_weight=0.2, alpha=1.0)
        wu.set_weight("win_strat", 0.2)
        new = wu.update_weight("win_strat")
        assert new > 0.2

    def test_smoothing(self):
        wu = WeightUpdater(tracker=self.tracker, base_weight=0.2, alpha=0.3)
        wu.set_weight("win_strat", 0.2)
        new = wu.update_weight("win_strat")
        target = self.tracker.strategy_stats("win_strat")
        assert target is not None
        raw_target = 0.2 * (target.adjusted_winrate / 0.55)
        expected = 0.2 * 0.7 + raw_target * 0.3
        assert math.isclose(new, expected, rel_tol=0.01)

    def test_weight_bounds(self):
        wu = WeightUpdater(tracker=self.tracker, base_weight=0.5, alpha=1.0,
                           min_weight=0.05, max_weight=0.50)
        wu.set_weight("win_strat", 0.5)
        new = wu.update_weight("win_strat")
        assert new <= 0.50

    def test_update_all(self):
        wu = WeightUpdater(tracker=self.tracker, alpha=1.0)
        wu.set_weight("win_strat", 0.2)
        results = wu.update_all()
        assert "win_strat" in results


class TestLearningEngine:
    def setup_method(self):
        reset_learning_engine()

    def test_shadow_mode(self):
        le = LearningEngine(shadow=True)
        assert le.shadow is True

    def test_record_result(self):
        le = LearningEngine(shadow=True)
        entry = le.record_result("strat_a", "BTC/USDT", "buy", 20000, 21000, 100)
        assert entry is not None
        assert entry.won is True
        assert le.total_trades == 1

    def test_top_strategies(self):
        le = LearningEngine(shadow=True)
        le.tracker.min_trades = 2  # Lower threshold for test
        for i in range(5):
            le.record_result("strat_a", "BTC/USDT", "buy", 20000 + i, 21000 + i, 100 + i)
        top = le.top_strategies()
        assert len(top) >= 1

    def test_get_set_weight(self):
        le = LearningEngine(shadow=True)
        le.set_weight("strat_a", 0.15)
        assert le.get_weight("strat_a") == 0.15

    def test_singleton(self):
        l1 = get_learning_engine(shadow=True)
        l2 = get_learning_engine(shadow=True)
        assert l1 is l2
