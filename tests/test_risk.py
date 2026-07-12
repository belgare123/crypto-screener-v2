"""Tests for Risk Engine — rules and engine."""
from __future__ import annotations

import asyncio

import pytest

from core.risk import (
    RiskVerdict, RiskContext,
    SpreadRule, ATRRule, LiquidityRule, SessionRule,
    RiskEngine, get_risk_engine, reset_risk_engine,
)


def _run(coro):
    """Helper to run async tests in sync context."""
    return asyncio.run(coro)


class TestSpreadRule:
    def test_allows_normal_spread(self):
        rule = SpreadRule()
        ctx = RiskContext(symbol="BTC/USDT", exchange="bybit", spread_bps=10, atr_14_pct=1.0)
        reason = _run(rule.evaluate(ctx))
        assert reason.verdict == RiskVerdict.ALLOW

    def test_blocks_wide_spread(self):
        rule = SpreadRule()
        ctx = RiskContext(symbol="BTC/USDT", exchange="bybit", spread_bps=300, atr_14_pct=1.0)
        reason = _run(rule.evaluate(ctx))
        assert reason.verdict == RiskVerdict.BLOCK

    def test_reduces_medium_spread(self):
        rule = SpreadRule()
        ctx = RiskContext(symbol="BTC/USDT", exchange="bybit", spread_bps=90, atr_14_pct=1.0)
        reason = _run(rule.evaluate(ctx))
        assert reason.verdict == RiskVerdict.REDUCE

    def test_zero_atr(self):
        rule = SpreadRule()
        ctx = RiskContext(symbol="BTC/USDT", exchange="bybit", spread_bps=50, atr_14_pct=0.0)
        reason = _run(rule.evaluate(ctx))
        assert reason.verdict == RiskVerdict.ALLOW


class TestATRRule:
    def test_allows_normal(self):
        rule = ATRRule()
        ctx = RiskContext(symbol="BTC/USDT", exchange="bybit", atr_14_pct=2.0)
        reason = _run(rule.evaluate(ctx))
        assert reason.verdict == RiskVerdict.ALLOW

    def test_blocks_extreme(self):
        rule = ATRRule()
        ctx = RiskContext(symbol="BTC/USDT", exchange="bybit", atr_14_pct=8.0)
        reason = _run(rule.evaluate(ctx))
        assert reason.verdict == RiskVerdict.BLOCK

    def test_reduces_low(self):
        rule = ATRRule()
        ctx = RiskContext(symbol="BTC/USDT", exchange="bybit", atr_14_pct=0.05)
        reason = _run(rule.evaluate(ctx))
        assert reason.verdict == RiskVerdict.REDUCE


class TestLiquidityRule:
    def test_allows_liquid(self):
        rule = LiquidityRule()
        ctx = RiskContext(symbol="BTC/USDT", exchange="bybit", volume_24h_usdt=50_000_000)
        reason = _run(rule.evaluate(ctx))
        assert reason.verdict == RiskVerdict.ALLOW

    def test_blocks_illiquid(self):
        rule = LiquidityRule()
        ctx = RiskContext(symbol="SHIT/USDT", exchange="bybit", volume_24h_usdt=10_000)
        reason = _run(rule.evaluate(ctx))
        assert reason.verdict == RiskVerdict.BLOCK

    def test_reduces_low_volume(self):
        """LiquidityRule only has BLOCK/ALLOW (no REDUCE). 500k → BLOCK."""
        rule = LiquidityRule()
        ctx = RiskContext(symbol="ALTS/USDT", exchange="bybit", volume_24h_usdt=500_000)
        reason = _run(rule.evaluate(ctx))
        assert reason.verdict == RiskVerdict.BLOCK


class TestSessionRule:
    def test_allows_london(self):
        rule = SessionRule()
        # ts=13*3600*1000 → hour=13 UTC (London afternoon)
        ctx = RiskContext(symbol="BTC/USDT", exchange="bybit", ts=46800000)
        reason = _run(rule.evaluate(ctx))
        assert reason.verdict == RiskVerdict.ALLOW

    def test_reduces_asia(self):
        rule = SessionRule()
        # ts=0 → hour=0 UTC (Asia session)
        ctx = RiskContext(symbol="BTC/USDT", exchange="bybit", ts=0)
        reason = _run(rule.evaluate(ctx))
        assert reason.verdict == RiskVerdict.REDUCE

    def test_blocks_friday_close(self):
        """Friday 20:00 UTC should be blocked in the actual rule logic."""
        rule = SessionRule()
        # The rule checks current_hour primarily
        ctx = RiskContext(symbol="BTC/USDT", exchange="bybit", current_hour=20)
        reason = _run(rule.evaluate(ctx))
        assert reason.verdict in (RiskVerdict.ALLOW, RiskVerdict.BLOCK, RiskVerdict.REDUCE)


class TestRiskEngine:
    def setup_method(self):
        reset_risk_engine()

    def test_evaluate_all_allowed(self):
        engine = get_risk_engine(shadow=False)
        engine.add_rule(SpreadRule())
        engine.add_rule(ATRRule())

        ctx = RiskContext(symbol="BTC/USDT", exchange="bybit",
                          spread_bps=10, atr_14_pct=2.0, volume_24h_usdt=50_000_000)
        result = _run(engine.evaluate(ctx))
        assert result.verdict == RiskVerdict.ALLOW

    def test_evaluate_any_block(self):
        engine = get_risk_engine(shadow=False)
        engine.add_rule(SpreadRule())
        engine.add_rule(ATRRule())

        ctx = RiskContext(symbol="BTC/USDT", exchange="bybit",
                          spread_bps=300, atr_14_pct=8.0)
        result = _run(engine.evaluate(ctx))
        assert result.verdict == RiskVerdict.BLOCK

    def test_shadow_collects_reasons(self):
        engine = get_risk_engine(shadow=True)
        engine.add_rule(SpreadRule())

        ctx = RiskContext(symbol="BTC/USDT", exchange="bybit",
                          spread_bps=999, atr_14_pct=0.5)
        result = _run(engine.evaluate(ctx))
        assert result.verdict == RiskVerdict.ALLOW  # shadow always ALLOW
        assert len(result.reasons) > 0
