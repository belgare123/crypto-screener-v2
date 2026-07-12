"""Tests for OME — PositionSizer, RiskManager, Tracker, Executor, OME facade."""
from __future__ import annotations

import math

import pytest

from core.ome import (
    OrderSide, OrderType, OrderStatus, Position, Order,
    PositionSizer, RiskManager, PositionTracker, OrderExecutor,
    OME, get_ome, reset_ome,
)


class TestPositionSizer:
    def test_calculate(self):
        s = PositionSizer(risk_pct=0.01, sl_mult=2.0, min_qty=0.001)
        qty = s.calculate(capital=1000, atr=50, price=20000)
        # risk=10, sl_dist=100, qty=0.1
        assert math.isclose(qty, 0.1, rel_tol=0.01)

    def test_zero_capital(self):
        s = PositionSizer()
        qty = s.calculate(capital=0, atr=50, price=20000)
        assert qty == 0.0

    def test_quantize(self):
        s = PositionSizer(step_size=0.01)
        qty = s.risk_value(0.1, 50)
        assert qty > 0

    def test_min_max(self):
        s = PositionSizer(risk_pct=1.0, sl_mult=1.0, max_qty=0.5)
        qty = s.calculate(capital=100000, atr=1, price=20000)
        assert qty <= 0.5

    def test_zero_atr(self):
        s = PositionSizer()
        qty = s.calculate(capital=1000, atr=0, price=20000)
        assert qty == 0.0


class TestRiskManager:
    def test_calculate_buy(self):
        r = RiskManager(sl_mult=2, tp_mult=3)
        result = r.calculate(entry_price=20000, atr=50, side="buy")
        assert math.isclose(result["stop_loss"], 19900, rel_tol=0.01)
        assert math.isclose(result["take_profit"], 20150, rel_tol=0.01)
        assert math.isclose(result["risk_reward_ratio"], 1.5, rel_tol=0.01)

    def test_calculate_sell(self):
        r = RiskManager(sl_mult=2, tp_mult=3)
        result = r.calculate(entry_price=20000, atr=50, side="sell")
        assert result["stop_loss"] > 20000
        assert result["take_profit"] < 20000
        assert result["risk_reward_ratio"] > 0

    def test_zero_atr(self):
        r = RiskManager()
        result = r.calculate(entry_price=20000, atr=0, side="buy")
        assert result["stop_loss"] == 0


class TestPositionTracker:
    def test_open_close(self):
        t = PositionTracker(shadow=True)
        pos = t.open("BTC/USDT", OrderSide.BUY, 0.1, 20000, stop_loss=19900)
        assert pos is not None
        assert t.count == 1
        t.close("BTC/USDT", 20100)
        assert t.count == 0

    def test_update_price(self):
        t = PositionTracker(shadow=True)
        t.open("BTC/USDT", OrderSide.BUY, 0.1, 20000)
        t.update_price("BTC/USDT", 20100)
        pos = t.get("BTC/USDT")
        assert pos is not None
        assert math.isclose(pos.unrealized_pnl, 10.0, rel_tol=0.01)

    def test_pnl_pct(self):
        t = PositionTracker(shadow=True)
        t.open("BTC/USDT", OrderSide.BUY, 1.0, 20000)
        t.update_price("BTC/USDT", 21000)
        pos = t.get("BTC/USDT")
        assert pos is not None
        assert math.isclose(pos.pnl_pct, 5.0, rel_tol=0.01)

    def test_duplicate_open(self):
        t = PositionTracker(shadow=True)
        t.open("BTC/USDT", OrderSide.BUY, 0.1, 20000)
        assert t.open("BTC/USDT", OrderSide.BUY, 0.1, 20000) is None

    def test_close_nonexistent(self):
        t = PositionTracker(shadow=True)
        assert t.close("NONEXISTENT") is None

    def test_total_exposure(self):
        t = PositionTracker(shadow=True)
        t.open("BTC/USDT", OrderSide.BUY, 1.0, 20000)
        t.update_price("BTC/USDT", 21000)
        assert math.isclose(t.total_exposure, 21000, rel_tol=0.01)


class TestOrderExecutor:
    def test_shadow_market(self):
        e = OrderExecutor(shadow=True)
        order = e.execute("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.1, 20000)
        assert order.status == OrderStatus.FILLED
        assert order.filled_qty == 0.1

    def test_shadow_limit(self):
        e = OrderExecutor(shadow=True)
        order = e.execute("BTC/USDT", OrderSide.SELL, OrderType.LIMIT, 0.5, 21000)
        assert order.status in (OrderStatus.OPEN, OrderStatus.FILLED)

    def test_track_orders(self):
        e = OrderExecutor(shadow=True)
        e.execute("BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.1, 20000)
        assert len(e.orders) == 1


class TestOME:
    def setup_method(self):
        reset_ome()

    def test_execute_signal(self):
        ome = get_ome(shadow=True, capital=1000, risk_pct=0.01)
        result = ome.execute_signal("BTC/USDT", "buy", 20000, 50)

        assert "order" in result
        assert "position" in result
        assert result["sizer"]["qty"] > 0
        assert result["risk"]["stop_loss"] < 20000  # buy → SL below

    def test_execute_sell(self):
        ome = get_ome(shadow=True, capital=1000, risk_pct=0.01)
        result = ome.execute_signal("BTC/USDT", "sell", 20000, 50)
        assert result["risk"]["stop_loss"] > 20000  # sell → SL above

    def test_zero_qty(self):
        ome = OME(shadow=True, capital=0)  # capital=0 → qty=0
        result = ome.execute_signal("BTC/USDT", "buy", 20000, 50)
        assert "error" in result
