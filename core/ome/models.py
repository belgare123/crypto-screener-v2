"""
OME — модели данных.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class Order:
    """Ордер в системе."""
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: float
    price: float  # лимитная цена или expected fill price
    stop_loss: float = 0.0
    take_profit: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    exchange_order_id: str = ""
    exchange: str = "bybit"
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    commission: float = 0.0
    pnl: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    """Открытая позиция."""
    symbol: str
    side: OrderSide
    size: float       # в базовой валюте (контрактах)
    entry_price: float
    current_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    opened_at: float = 0.0
    updated_at: float = 0.0
    orders: list[Order] = field(default_factory=list)
    exchange: str = "bybit"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def pnl_pct(self) -> float:
        if self.entry_price == 0:
            return 0.0
        if self.side == OrderSide.BUY:
            return (self.current_price - self.entry_price) / self.entry_price * 100
        else:
            return (self.entry_price - self.current_price) / self.entry_price * 100

    @property
    def distance_to_sl_pct(self) -> float:
        if self.entry_price == 0 or self.stop_loss == 0:
            return 0.0
        if self.side == OrderSide.BUY:
            return (self.current_price - self.stop_loss) / self.entry_price * 100
        else:
            return (self.stop_loss - self.current_price) / self.entry_price * 100

    @property
    def distance_to_tp_pct(self) -> float:
        if self.entry_price == 0 or self.take_profit == 0:
            return 0.0
        if self.side == OrderSide.BUY:
            return (self.take_profit - self.current_price) / self.entry_price * 100
        else:
            return (self.current_price - self.take_profit) / self.entry_price * 100

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "size": self.size,
            "entry_price": self.entry_price,
            "current_price": self.current_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "pnl_pct": round(self.pnl_pct, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "opened_at": self.opened_at,
        }
