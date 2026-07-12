"""Tests for State Engine — state dimensions and MarketStateSnapshot."""
from __future__ import annotations

import pytest

from core.state import MarketStateSnapshot, StateEngine, get_state_engine
from core.state.market_state import (
    TrendDimension, VolatilityDimension, LiquidityDimension,
    NoiseDimension, ParticipationDimension,
)


class TestTrendDimension:
    def test_default(self):
        d = TrendDimension()
        assert d.label == "sideways"
        assert d.score == 0.0

    def test_custom(self):
        d = TrendDimension(label="strong_up", score=85, adx=35)
        assert d.label == "strong_up"
        assert d.score == 85
        assert d.adx == 35


class TestVolatilityDimension:
    def test_default(self):
        d = VolatilityDimension()
        assert d.label == "normal"

    def test_high(self):
        d = VolatilityDimension(label="high", score=75, atr_pct=3.5)
        assert d.atr_pct == 3.5


class TestLiquidityDimension:
    def test_default(self):
        d = LiquidityDimension()
        assert d.label == "normal"

    def test_low(self):
        d = LiquidityDimension(label="low", score=20, spread_pct=0.5)
        assert d.spread_pct == 0.5


class TestNoiseDimension:
    def test_default(self):
        d = NoiseDimension()
        assert d.label == "normal"
        assert d.is_noisy is False

    def test_noisy(self):
        d = NoiseDimension(label="high", score=85, noise_pct=0.7, is_noisy=True)
        assert d.is_noisy is True
        assert d.noise_pct == 0.7


class TestParticipationDimension:
    def test_default(self):
        d = ParticipationDimension()
        assert d.label == "balanced"

    def test_institutional(self):
        d = ParticipationDimension(label="institutional", score=85, oi_change_pct=5.0)
        assert d.oi_change_pct == 5.0


class TestMarketStateSnapshot:
    def test_default(self):
        s = MarketStateSnapshot()
        assert s.symbol == ""
        assert s.regime == "neutral"
        assert s.is_ready is False

    def test_minimal(self):
        s = MarketStateSnapshot(
            symbol="BTC/USDT",
            exchange="bybit",
            timestamp=1234567890,
        )
        assert s.symbol == "BTC/USDT"
        assert len(s.trend.meta) == 0

    def test_full_state(self):
        s = MarketStateSnapshot(
            symbol="BTC/USDT",
            exchange="bybit",
            timestamp=1000,
            trend=TrendDimension(label="strong_up", score=80, adx=30),
            volatility=VolatilityDimension(label="high", score=70, atr_pct=2.5),
            noise=NoiseDimension(label="low", score=20, is_noisy=False),
            regime="bull",
            confidence=75.0,
            is_ready=True,
        )
        assert s.regime == "bull"
        assert s.trend.label == "strong_up"
        assert s.confidence == 75.0
        assert s.is_ready is True

    def test_to_dict(self):
        s = MarketStateSnapshot(
            symbol="BTC/USDT",
            timestamp=1000,
            trend=TrendDimension(label="strong_up", score=80),
            volatility=VolatilityDimension(label="high", score=70),
            noise=NoiseDimension(label="low", score=20),
            liquidity=LiquidityDimension(label="normal", score=50),
            participation=ParticipationDimension(label="balanced", score=50),
            regime="bull",
            confidence=75.0,
            is_ready=True,
        )
        d = s.to_dict()
        assert d["symbol"] == "BTC/USDT"
        assert d["trend"]["label"] == "strong_up"
        assert d["trend"]["score"] == 80
        assert d["volatility"]["label"] == "high"
        assert d["regime"] == "bull"
        assert d["is_ready"] is True

    def test_repr(self):
        s = MarketStateSnapshot(symbol="BTC/USDT")
        assert "BTC/USDT" in repr(s)
