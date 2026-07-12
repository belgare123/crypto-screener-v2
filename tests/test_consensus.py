"""Tests for Consensus Engine — weighted voting and ranking."""
from __future__ import annotations

import pytest

from core.consensus import (
    SignalDirection, SignalVote, ConsensusResult,
    ConsensusEngine, get_consensus_engine, reset_consensus_engine,
    OpportunityRanking,
)


class TestSignalVote:
    def test_default_weight(self):
        v = SignalVote(strategy_name="ema_cross", direction=SignalDirection.BUY, confidence=0.7)
        assert v.strategy_name == "ema_cross"
        assert v.weight == 1.0

    def test_custom_weight(self):
        v = SignalVote(strategy_name="bbands", direction=SignalDirection.SELL,
                       confidence=0.6, weight=0.5)
        assert v.weight == 0.5

    def test_confidence(self):
        v = SignalVote(strategy_name="rsi", direction=SignalDirection.BUY, confidence=0.8)
        assert v.confidence == 0.8

    def test_extra(self):
        v = SignalVote(strategy_name="ema", direction=SignalDirection.BUY,
                       confidence=0.7, extra={"symbol": "BTC/USDT"})
        assert v.extra["symbol"] == "BTC/USDT"


class TestConsensusResult:
    def test_buy_ratio(self):
        result = ConsensusResult(symbol="BTC/USDT", direction=SignalDirection.BUY,
                                 buy_votes=7, sell_votes=1, total_votes=10)
        assert result.buy_ratio == 0.7

    def test_meaningful_high(self):
        result = ConsensusResult(symbol="BTC/USDT", direction=SignalDirection.BUY,
                                 total_votes=5, confidence=0.8)
        assert result.meaningful is True

    def test_not_meaningful_few_votes(self):
        result = ConsensusResult(symbol="BTC/USDT", direction=SignalDirection.BUY,
                                 total_votes=1, confidence=0.8)
        assert result.meaningful is False


class TestConsensusEngine:
    def setup_method(self):
        reset_consensus_engine()

    def _vote(self, name, direction, confidence=0.8, score=80, weight=1.0, symbol="BTC/USDT"):
        return SignalVote(strategy_name=name, direction=direction,
                          confidence=confidence, score=score, weight=weight,
                          extra={"symbol": symbol})

    def test_weighted_buy(self):
        engine = get_consensus_engine(shadow=False)
        votes = [
            self._vote("ema_cross", SignalDirection.BUY, 0.8, 80),
            self._vote("rsi", SignalDirection.BUY, 0.7, 70),
            self._vote("bbands", SignalDirection.SELL, 0.3, 30),
        ]
        result = engine.evaluate(votes)
        assert result.direction == SignalDirection.BUY
        assert result.score > 0
        assert result.symbol == "BTC/USDT"

    def test_neutral_on_equal_votes(self):
        engine = get_consensus_engine(shadow=False)
        votes = [
            self._vote("strat_a", SignalDirection.BUY, 0.5, 50, weight=1.0, symbol="ETH/USDT"),
            self._vote("strat_b", SignalDirection.SELL, 0.5, 50, weight=1.0, symbol="ETH/USDT"),
        ]
        result = engine.evaluate(votes)
        assert result.direction == SignalDirection.NEUTRAL

    def test_shadow(self):
        engine = get_consensus_engine(shadow=True)
        votes = [self._vote("strat_a", SignalDirection.BUY)]
        result = engine.evaluate(votes)
        assert result.direction == SignalDirection.BUY

    def test_regime_multiplier(self):
        engine = get_consensus_engine(shadow=False)
        votes = [self._vote("trend_follow", SignalDirection.BUY)]
        result = engine.evaluate(votes, regime_multiplier=2.0)
        assert result.regime_multiplier > 1.0

    def test_empty_votes(self):
        engine = get_consensus_engine(shadow=False)
        result = engine.evaluate([])
        assert result.direction == SignalDirection.NEUTRAL
        assert result.score == 0.0


class TestOpportunityRanking:
    def test_rank_top(self):
        rank = OpportunityRanking(window_minutes=10, top_k=2)
        rank.add_result(ConsensusResult(symbol="BTC/USDT", direction=SignalDirection.BUY,
                                         score=85, buy_votes=9, sell_votes=0, total_votes=9))
        rank.add_result(ConsensusResult(symbol="ETH/USDT", direction=SignalDirection.BUY,
                                         score=70, buy_votes=5, sell_votes=2, total_votes=7))
        rank.add_result(ConsensusResult(symbol="SOL/USDT", direction=SignalDirection.BUY,
                                         score=60, buy_votes=3, sell_votes=3, total_votes=6))

        top = rank.get_top()
        assert len(top) == 2
        assert top[0].symbol == "BTC/USDT"
        assert top[1].symbol == "ETH/USDT"

    def test_empty_ranking(self):
        rank = OpportunityRanking()
        assert rank.get_top() == []

    def test_active_count(self):
        rank = OpportunityRanking(window_minutes=10)
        assert rank.active_count == 0
        rank.add_result(ConsensusResult(symbol="BTC/USDT", direction=SignalDirection.BUY,
                                         score=85, buy_votes=5, sell_votes=0, total_votes=5))
        assert rank.active_count == 1

    def test_clear(self):
        rank = OpportunityRanking()
        rank.add_result(ConsensusResult(symbol="BTC/USDT", direction=SignalDirection.BUY,
                                         score=85, buy_votes=5, sell_votes=0, total_votes=5))
        assert rank.active_count == 1
        rank.clear()
        assert rank.active_count == 0
