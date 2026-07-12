"""
PositionTracker — отслеживание открытых позиций.

Потокобезопасный. Хранит Position[symbol], позволяет:
- open() — открыть новую позицию
- close() — закрыть позицию
- update() — обновить current_price/unrealized_pnl
- get() — получить позицию по symbol
- all() — все открытые позиции
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from core.ome.models import OrderSide, OrderStatus, Position, Order

logger = logging.getLogger(__name__)


class PositionTracker:
    """Трекер открытых позиций."""

    def __init__(self, shadow: bool = True):
        self.shadow = shadow
        self._lock = threading.Lock()
        self._positions: dict[str, Position] = {}  # symbol → Position

    # ── CRUD ──

    def open(self, symbol: str, side: OrderSide, size: float,
             entry_price: float, stop_loss: float = 0.0,
             take_profit: float = 0.0, exchange: str = "bybit",
             extra: dict | None = None) -> Position | None:
        """Открыть новую позицию.

        Если уже есть открытая по symbol — возвращает None (лог ошибка).
        """
        with self._lock:
            if symbol in self._positions:
                logger.warning("[tracker] Position already open for %s", symbol)
                return None

            pos = Position(
                symbol=symbol,
                side=side,
                size=size,
                entry_price=entry_price,
                current_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                opened_at=time.time(),
                updated_at=time.time(),
                exchange=exchange,
                extra=extra or {},
            )
            self._positions[symbol] = pos
            if not self.shadow:
                logger.info("[tracker] OPEN %s %s %.4f @ %.2f (SL=%.2f TP=%.2f)",
                           side.value, symbol, size, entry_price, stop_loss, take_profit)
            else:
                logger.debug("[tracker] SHADOW OPEN %s %s %.4f @ %.2f", side.value, symbol, size, entry_price)
            return pos

    def close(self, symbol: str, price: float = 0.0) -> Position | None:
        """Закрыть позицию и вернуть её."""
        with self._lock:
            pos = self._positions.pop(symbol, None)
            if pos is None:
                logger.warning("[tracker] No position to close for %s", symbol)
                return None

            if price > 0:
                pos.current_price = price
            pos.updated_at = time.time()

            if not self.shadow:
                logger.info("[tracker] CLOSE %s %.4f @ %.2f PnL=%.2f",
                           symbol, pos.size, pos.current_price, pos.unrealized_pnl)
            return pos

    def update_price(self, symbol: str, price: float):
        """Обновить current_price + unrealized PnL."""
        with self._lock:
            pos = self._positions.get(symbol)
            if pos is None:
                return
            pos.current_price = price
            pos.updated_at = time.time()
            # unrealized PnL — упрощённо: (current - entry) × size
            if pos.side == OrderSide.BUY:
                pos.unrealized_pnl = (price - pos.entry_price) * pos.size
            else:
                pos.unrealized_pnl = (pos.entry_price - price) * pos.size

    def get(self, symbol: str) -> Position | None:
        """Получить позицию по symbol."""
        with self._lock:
            return self._positions.get(symbol)

    def all(self) -> list[Position]:
        """Все открытые позиции."""
        with self._lock:
            return list(self._positions.values())

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._positions)

    @property
    def total_pnl(self) -> float:
        with self._lock:
            return sum(p.unrealized_pnl for p in self._positions.values())

    @property
    def total_exposure(self) -> float:
        with self._lock:
            return sum(p.size * p.current_price for p in self._positions.values())

    def reset(self):
        """Сброс (для тестов)."""
        with self._lock:
            self._positions.clear()
