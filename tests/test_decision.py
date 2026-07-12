"""
Tests for Decision Layer — core/decision/engine.py and core/decision/models.py.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.decision.models import Decision
from core.decision.engine import DecisionEngine
from core.consensus.models import ConsensusResult, SignalDirection, SignalVote


# ── Fixtures ──


@pytest.fixture
def mock_consensus():
    """Базовый ConsensusResult для тестов (BUY, conf=0.75)."""
    return ConsensusResult(
        symbol="BTC/USDT:USDT",
        direction=SignalDirection.BUY,
        confidence=0.75,
        score=75.0,
        votes=[],
        total_votes=3,
        buy_votes=3,
        sell_votes=0,
    )


@pytest.fixture
def mock_weak_consensus():
    """Слабый сигнал (conf=0.45)."""
    return ConsensusResult(
        symbol="BTC/USDT:USDT",
        direction=SignalDirection.BUY,
        confidence=0.45,
        score=45.0,
        votes=[],
        total_votes=3,
        buy_votes=2,
        sell_votes=1,
    )


@pytest.fixture
def mock_neutral_consensus():
    """Нейтральный сигнал."""
    return ConsensusResult(
        symbol="BTC/USDT:USDT",
        direction=SignalDirection.NEUTRAL,
        confidence=0.5,
        score=50.0,
        votes=[],
        total_votes=3,
        buy_votes=1,
        sell_votes=1,
    )


@pytest.fixture
def mock_context_engine():
    """ContextEngine с предопределённым контекстом."""
    ce = AsyncMock()
    ctx = MagicMock()
    ctx.trend = "bull"
    ctx.volatility = "normal"
    ctx.volatility_state = "contraction"
    ctx.to_dict.return_value = {"trend": "bull", "volatility": "normal"}
    ce.get_context = AsyncMock(return_value=ctx)
    return ce


@pytest.fixture
def mock_feature_store():
    """FeatureStore с предопределёнными ценами/ATR."""
    fs = AsyncMock()

    async def get(symbol, key):
        data = {
            "BTC/USDT:USDT": {
                "ticker.last": 50000.0,
                "vol.atr_pct": 1.5,
            }
        }
        return data.get(symbol, {}).get(key)

    fs.get = AsyncMock(side_effect=get)
    return fs


@pytest.fixture
def decision_engine(mock_context_engine, mock_feature_store):
    """DecisionEngine с замоканными зависимостями."""
    return DecisionEngine(
        context_engine=mock_context_engine,
        consensus_engine=None,
        feature_store=mock_feature_store,
        default_threshold=60.0,
    )


# ── Decision Model ──


class TestDecisionModel:
    def test_default_action_is_none(self):
        d = Decision(symbol="BTC/USDT:USDT")
        assert d.action == "none"
        assert not d.is_actionable

    def test_buy_actionable(self):
        d = Decision(symbol="BTC/USDT:USDT", action="buy")
        assert d.is_actionable

    def test_sell_actionable(self):
        d = Decision(symbol="BTC/USDT:USDT", action="sell")
        assert d.is_actionable

    def test_to_dict_full(self):
        d = Decision(
            symbol="BTC/USDT:USDT",
            action="buy",
            confidence=0.75,
            score=75.0,
            sl=49000.0,
            tp=53000.0,
            reason="pass (threshold adj=60%)",
            threshold=60.0,
            meta={"votes": 3},
        )
        data = d.to_dict()
        assert data["symbol"] == "BTC/USDT:USDT"
        assert data["action"] == "buy"
        assert data["sl"] == 49000.0
        assert data["tp"] == 53000.0
        assert data["threshold"] == 60.0
        assert data["actionable"] is True

    def test_to_dict_none_action(self):
        d = Decision(symbol="BTC/USDT:USDT", action="none", reason="low_confidence")
        data = d.to_dict()
        assert not data["actionable"]

    def test_short_reason_actionable(self):
        d = Decision(
            symbol="BTC/USDT:USDT", action="buy",
            reason="pass", sl=49000.0, tp=53000.0,
        )
        assert "SL=49000.0" in d.short_reason
        assert "TP=53000.0" in d.short_reason

    def test_short_reason_none(self):
        d = Decision(symbol="BTC/USDT:USDT", action="none", reason="low_confidence")
        assert d.short_reason == "low_confidence"

    def test_direction_alias(self):
        d = Decision(symbol="BTC/USDT:USDT", action="sell")
        assert d.direction == "sell"


# ── Adaptive Thresholds ──


class TestAdaptiveThreshold:
    """Проверка _calc_threshold для разных состояний рынка."""

    def _make_ctx(self, trend="bull", volatility="normal", volatility_state="contraction"):
        ctx = MagicMock()
        ctx.trend = trend
        ctx.volatility = volatility
        ctx.volatility_state = volatility_state
        return ctx

    def test_default_threshold(self, decision_engine):
        ctx = self._make_ctx(trend="bull", volatility="normal", volatility_state="contraction")
        t = decision_engine._calc_threshold(ctx)
        # bull (−3) + normal = 57
        assert t == 57.0

    def test_flat_increases_threshold(self, decision_engine):
        ctx = self._make_ctx(trend="flat", volatility="normal")
        t = decision_engine._calc_threshold(ctx)
        # base 60 + flat 10 - bull 0 = 70
        assert t == 70.0

    def test_high_volatility_increases(self, decision_engine):
        ctx = self._make_ctx(trend="bull", volatility="high")
        t = decision_engine._calc_threshold(ctx)
        # base 60 + bull −3 + high 5 = 62
        assert t == 62.0

    def test_extreme_volatility_increases_more(self, decision_engine):
        ctx = self._make_ctx(trend="bull", volatility="extreme")
        t = decision_engine._calc_threshold(ctx)
        # base 60 + bull −3 + extreme 8 = 65
        assert t == 65.0

    def test_flat_and_high_volatility(self, decision_engine):
        ctx = self._make_ctx(trend="flat", volatility="high")
        t = decision_engine._calc_threshold(ctx)
        # base 60 + flat 10 + high 5 = 75
        assert t == 75.0

    def test_expansion_adds_threshold(self, decision_engine):
        ctx = self._make_ctx(trend="bull", volatility="normal", volatility_state="expansion")
        t = decision_engine._calc_threshold(ctx)
        # base 60 + bull −3 + expansion 5 = 62
        assert t == 62.0

    def test_cap_at_95(self, decision_engine):
        ctx = self._make_ctx(trend="flat", volatility="extreme", volatility_state="expansion")
        t = decision_engine._calc_threshold(ctx)
        # base 60 + flat 10 + extreme 8 + expansion 5 = 83 → < 95, OK
        assert t <= 95.0
        assert t == 83.0


# ── DecisionEngine.evaluate ──


class TestDecisionEngineEvaluate:
    @pytest.mark.asyncio
    async def test_buy_passes_with_good_confidence(self, decision_engine, mock_consensus):
        decision = await decision_engine.evaluate("BTC/USDT:USDT", mock_consensus)
        assert decision.action == "buy"
        assert decision.is_actionable
        assert decision.confidence == 0.75
        assert decision.score == 75.0

    @pytest.mark.asyncio
    async def test_weak_signal_rejected(self, decision_engine, mock_weak_consensus):
        decision = await decision_engine.evaluate("BTC/USDT:USDT", mock_weak_consensus)
        assert decision.action == "none"
        assert not decision.is_actionable
        assert "low_confidence" in decision.reason

    @pytest.mark.asyncio
    async def test_neutral_signal_rejected(self, decision_engine, mock_neutral_consensus):
        decision = await decision_engine.evaluate("BTC/USDT:USDT", mock_neutral_consensus)
        assert decision.action == "none"
        assert not decision.is_actionable
        assert "neutral" in decision.reason

    @pytest.mark.asyncio
    async def test_dynamic_sltp_calculated(self, decision_engine, mock_consensus):
        decision = await decision_engine.evaluate("BTC/USDT:USDT", mock_consensus)
        assert decision.is_actionable
        assert decision.sl is not None
        assert decision.tp is not None
        # BTC at 50000, ATR% = 1.5 → ATR value = 750
        # SL = 50000 - 1.5 * 750 = 48875
        # TP = 50000 + 3.0 * 750 = 52250
        assert decision.sl == 48875.0
        assert decision.tp == 52250.0

    @pytest.mark.asyncio
    async def test_dynamic_sltp_no_data(self, decision_engine, mock_consensus):
        """FeatureStore возвращает None — SL/TP = None."""
        decision_engine._fs.get = AsyncMock(return_value=None)
        decision = await decision_engine.evaluate("BTC/USDT:USDT", mock_consensus)
        assert decision.is_actionable
        assert decision.sl is None
        assert decision.tp is None

    @pytest.mark.asyncio
    async def test_meta_contains_context(self, decision_engine, mock_consensus):
        decision = await decision_engine.evaluate("BTC/USDT:USDT", mock_consensus)
        assert "context" in decision.meta
        assert decision.meta["context"]["trend"] == "bull"

    @pytest.mark.asyncio
    async def test_threshold_stored(self, decision_engine, mock_consensus):
        decision = await decision_engine.evaluate("BTC/USDT:USDT", mock_consensus)
        # bull trend → base 60 - 3 = 57
        assert decision.threshold == 57.0


# ── evaluate_batch ──


class TestDecisionEngineBatch:
    @pytest.mark.asyncio
    async def test_batch(self, decision_engine, mock_consensus, mock_weak_consensus):
        results = await decision_engine.evaluate_batch({
            "BTC/USDT:USDT": mock_consensus,
            "ETH/USDT:USDT": mock_weak_consensus,
        })
        assert results["BTC/USDT:USDT"].is_actionable
        assert not results["ETH/USDT:USDT"].is_actionable

    @pytest.mark.asyncio
    async def test_batch_error_graceful(self, decision_engine, mock_consensus):
        """Падение evaluate для одного символа не ломает весь batch."""
        decision_engine._ce.get_context = AsyncMock(side_effect=ValueError("boom"))
        results = await decision_engine.evaluate_batch({
            "BTC/USDT:USDT": mock_consensus,
        })
        assert not results["BTC/USDT:USDT"].is_actionable
        assert "error" in results["BTC/USDT:USDT"].reason


# ── Sell signal ──


class TestSellSignal:
    @pytest.mark.asyncio
    async def test_sell_passes(self, decision_engine, mock_feature_store):
        sell = ConsensusResult(
            symbol="BTC/USDT:USDT",
            direction=SignalDirection.SELL,
            confidence=0.8,
            score=80.0,
            votes=[],
            total_votes=3,
        )
        decision = await decision_engine.evaluate("BTC/USDT:USDT", sell)
        assert decision.action == "sell"
        assert decision.is_actionable
        # SELL: SL = price + 1.5 * ATR = 50000 + 1125 = 51125
        #       TP = price - 3.0 * ATR = 50000 - 2250 = 47750
        assert decision.sl is not None
        assert decision.tp is not None
        assert decision.sl > 50000  # SL above price for sell
        assert decision.tp < 50000  # TP below price for sell


# ── Shadow Mode ──


class TestShadowMode:
    @pytest.mark.asyncio
    async def test_shadow_by_default(self, decision_engine, mock_consensus):
        assert decision_engine.shadow is True
        # Просто проверяем, что не падает
        decision = await decision_engine.evaluate("BTC/USDT:USDT", mock_consensus)
        assert decision.is_actionable
