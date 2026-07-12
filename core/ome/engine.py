"""
OME — Order Management Engine facade.

Объединяет PositionSizer + RiskManager + PositionTracker + OrderExecutor.
Цельный поток: signal → OME.execute(signal).
"""
from __future__ import annotations

import logging
import threading
import time

from core.ome.models import OrderSide, OrderType
from core.ome.sizer import PositionSizer
from core.ome.risk_manager import RiskManager
from core.ome.tracker import PositionTracker
from core.ome.executor import OrderExecutor

logger = logging.getLogger(__name__)


class OME:
    """Фасад Order Management Engine."""

    def __init__(self, exchange=None, shadow: bool = True,
                 capital: float = 1000.0, risk_pct: float = 0.01):
        self.shadow = shadow
        self.capital = capital
        self.sizer = PositionSizer(risk_pct=risk_pct)
        self.risk_mgr = RiskManager()
        self.tracker = PositionTracker(shadow=shadow)
        self.executor = OrderExecutor(exchange=exchange, shadow=shadow)

    def execute_signal(self, symbol: str, side: str, price: float,
                       atr: float,
                       override_sl: float | None = None,
                       override_tp: float | None = None) -> dict:
        """Полный pipeline signal → order.

        Steps:
        1. RiskManager → SL/TP (или override_sl/override_tp если переданы)
        2. PositionSizer → qty
        3. PositionTracker.open(...)
        4. OrderExecutor.execute(...) → Order

        Args:
            override_sl: динамический SL от DecisionEngine (переопределяет risk_mgr)
            override_tp: динамический TP от DecisionEngine (переопределяет risk_mgr)

        Returns:
            dict {order, position, sizer, risk}
        """
        # 1. SL/TP
        risk = self.risk_mgr.calculate(price, atr, side)

        # DecisionEngine override
        if override_sl is not None:
            risk["stop_loss"] = override_sl
        if override_tp is not None:
            risk["take_profit"] = override_tp

        # 2. Qty
        qty = self.sizer.calculate(self.capital, atr, price, side)
        if qty <= 0:
            logger.warning("[ome] qty=0 for %s %s — skipping", side, symbol)
            return {"error": "qty=0", "sizer": {"qty": 0}, "risk": risk}

        # 3. Track
        oside = OrderSide.BUY if side == "buy" else OrderSide.SELL
        pos = self.tracker.open(
            symbol=symbol,
            side=oside,
            size=qty,
            entry_price=price,
            stop_loss=risk["stop_loss"],
            take_profit=risk["take_profit"],
        )

        # 4. Execute
        order = self.executor.execute(
            symbol=symbol,
            side=oside,
            order_type=OrderType.MARKET,
            qty=qty,
            price=price,
            stop_loss=risk["stop_loss"],
            take_profit=risk["take_profit"],
        )

        return {
            "order": order,
            "position": pos,
            "sizer": {"qty": qty, "risk_amount": self.sizer.risk_value(qty, atr)},
            "risk": risk,
        }

    @property
    def positions(self) -> list:
        return self.tracker.all()

    @property
    def total_pnl(self) -> float:
        return self.tracker.total_pnl

    @property
    def total_exposure(self) -> float:
        return self.tracker.total_exposure

    async def update_positions(self, price: float, timestamp: float | None = None) -> list[dict]:
        """
        Проверить позиции на SL/TP. Закрыть при достижении.

        Returns
        -------
        list[dict]
            Список закрытых сделок с информацией о PnL.
        """
        closed: list[dict] = []
        for pos in self.tracker.all():
            side_value = pos.side.value if hasattr(pos.side, "value") else str(pos.side)

            if side_value == "buy":
                hit_sl = pos.stop_loss > 0 and price <= pos.stop_loss
                hit_tp = pos.take_profit > 0 and price >= pos.take_profit
            else:
                hit_sl = pos.stop_loss > 0 and price >= pos.stop_loss
                hit_tp = pos.take_profit > 0 and price <= pos.take_profit

            if not (hit_sl or hit_tp):
                continue

            exit_reason = "SL" if hit_sl else "TP"
            # Manual PnL (unrealized_pnl is a dataclass field, not computed)
            pnl = (
                (price - pos.entry_price) * pos.size
                if side_value == "buy"
                else (pos.entry_price - price) * pos.size
            )
            self.tracker.close(pos.symbol, price=price)
            trade = {
                "ts": timestamp or 0,
                "symbol": pos.symbol,
                "side": side_value,
                "exit_price": round(price, 2),
                "entry_price": round(pos.entry_price, 2),
                "qty": pos.size,
                "pnl": round(pnl, 2),
                "exit_reason": exit_reason,
            }
            closed.append(trade)
            logger.info(
                "[ome] CLOSE %s %s @ %.2f  PnL=%.4f",
                exit_reason, pos.symbol, price, pnl,
            )
        return closed


# Singleton
_ome: OME | None = None


def get_ome(exchange=None, shadow: bool = True,
            capital: float = 1000.0, risk_pct: float = 0.01) -> OME:
    global _ome
    if _ome is None:
        _ome = OME(exchange=exchange, shadow=shadow,
                   capital=capital, risk_pct=risk_pct)
    return _ome


def reset_ome():
    global _ome
    _ome = None
