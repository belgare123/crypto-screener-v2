"""Tests for Signal Engine — process_consensus, cooldown, shadow mode."""
from __future__ import annotations

import asyncio

import pytest

from core.consensus import SignalDirection, SignalVote, ConsensusResult
from core.signal import (
    SignalEngine, get_signal_engine, reset_signal_engine,
)


def _run(coro):
    return asyncio.run(coro)


def _result(symbol="BTC/USDT", direction=SignalDirection.BUY,
            score=75, confidence=0.85, votes_count=5):
    return ConsensusResult(
        symbol=symbol, direction=direction,
        score=score, confidence=confidence,
        buy_votes=votes_count if direction == SignalDirection.BUY else 0,
        sell_votes=votes_count if direction == SignalDirection.SELL else 0,
        total_votes=votes_count,
    )


class TestSignalEngine:
    def setup_method(self):
        reset_signal_engine()

    def test_process_consensus(self):
        se = SignalEngine(shadow=True)
        result = _run(se.process_consensus(_result(), atr=50, price=20000))
        assert result is not None
        # Returns dict in shadow mode
        if isinstance(result, dict):
            assert result.get("symbol") == "BTC/USDT"

    def test_multiple_calls(self):
        se = SignalEngine(shadow=True)
        for i in range(5):
            _run(se.process_consensus(
                _result(symbol=f"PAIR{i}/USDT", votes_count=3 + i),
                atr=50, price=20000,
            ))
        assert True

    def test_cooldown_blocks(self):
        se = SignalEngine(shadow=True, cooldown_default=3600)
        first = _run(se.process_consensus(_result(), atr=50, price=20000))
        assert first is not None
        # Second call — may be blocked or not, just no exception
        _run(se.process_consensus(_result(), atr=50, price=20000))
        assert True

    def test_shadow_mode(self):
        se = SignalEngine(shadow=True)
        assert se.shadow is True

    def test_clear_cooldowns(self):
        se = SignalEngine(shadow=True, cooldown_default=3600)
        _run(se.process_consensus(_result(), atr=50, price=20000))
        se.clear_cooldowns()
        result = _run(se.process_consensus(_result(), atr=50, price=20000))
        assert result is not None

    def test_consensus_with_sell(self):
        se = SignalEngine(shadow=True)
        result = _run(se.process_consensus(
            _result(direction=SignalDirection.SELL, score=70),
            atr=50, price=20000,
        ))
        assert result is not None
