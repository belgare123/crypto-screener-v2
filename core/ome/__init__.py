"""
OME — Order Management Engine.

Компоненты:
- PositionSizer — расчёт размера позиции (capital × risk% / (ATR × sl_mult))
- RiskManager — SL = entry - ATR×2, TP = entry + ATR×3
- PositionTracker — одна позиция на symbol
- OrderExecutor — отправка ордеров через REST API биржи

Все компоненты работают в shadow-mode до полной верификации.
"""
from __future__ import annotations

from .models import OrderSide, OrderType, OrderStatus, Position, Order
from .sizer import PositionSizer
from .risk_manager import RiskManager
from .tracker import PositionTracker
from .executor import OrderExecutor
from .engine import OME, get_ome, reset_ome

__all__ = [
    "OrderSide", "OrderType", "OrderStatus", "Position", "Order",
    "PositionSizer",
    "RiskManager",
    "PositionTracker",
    "OrderExecutor",
    "OME", "get_ome", "reset_ome",
]
