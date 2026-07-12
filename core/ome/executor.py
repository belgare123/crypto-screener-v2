"""
OrderExecutor — отправка ордеров через REST API биржи.

В shadow-mode логирует, но не отправляет реальные ордера.
При выходе из shadow — использует exchange.rest_order().

Поддерживает MARKET и LIMIT ордера с SL/TP.
"""
from __future__ import annotations

import logging
import time

from core.ome.models import OrderSide, OrderType, OrderStatus, Order

logger = logging.getLogger(__name__)


class OrderExecutor:
    """Исполнитель ордеров."""

    def __init__(self, exchange=None, shadow: bool = True):
        """
        Args:
            exchange: объект биржи с методом create_order(...)
            shadow: если True — ордера не отправляются реально
        """
        self.exchange = exchange
        self.shadow = shadow
        self._orders: list[Order] = []
        self._order_id_counter = 0

    def execute(self, symbol: str, side: OrderSide, order_type: OrderType,
                qty: float, price: float = 0.0,
                stop_loss: float = 0.0, take_profit: float = 0.0) -> Order:
        """Создать и (если не shadow) отправить ордер.

        Returns:
            Order с финальным статусом.
        """
        self._order_id_counter += 1
        now = time.time()

        order = Order(
            symbol=symbol,
            side=side,
            order_type=order_type,
            qty=qty,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status=OrderStatus.PENDING,
            exchange="bybit",
            created_at=now,
            updated_at=now,
            extra={"shadow": self.shadow, "internal_id": self._order_id_counter},
        )

        if self.shadow:
            order.status = OrderStatus.FILLED if order_type == OrderType.MARKET else OrderStatus.OPEN
            order.filled_qty = qty
            order.avg_fill_price = price if price > 0 else price  # симуляция
            logger.info("[executor] SHADOW %s %s %.4f @ %.2f (SL=%.2f TP=%.2f)",
                       side.value, symbol, qty, price, stop_loss, take_profit)
        else:
            try:
                result = self._place_real_order(order)
                if result:
                    order.exchange_order_id = result.get("order_id", "")
                    order.status = OrderStatus.OPEN
                    logger.info("[executor] REAL %s %s %.4f id=%s",
                               side.value, symbol, qty, order.exchange_order_id)
                else:
                    order.status = OrderStatus.REJECTED
                    logger.error("[executor] REJECTED %s %s %.4f", side.value, symbol, qty)
            except Exception as e:
                order.status = OrderStatus.REJECTED
                logger.exception("[executor] ERROR %s %s %.4f: %s", side.value, symbol, qty, e)

        self._orders.append(order)
        return order

    def _place_real_order(self, order: Order) -> dict | None:
        """Реальная отправка ордера — вызов exchange."""
        if self.exchange is None:
            logger.error("[executor] No exchange set — cannot place real order")
            return None

        # TODO: адаптировать под API биржи (Bybit v5 REST)
        # exchange.create_order(symbol, side, type, qty, price, stop_loss, take_profit)
        try:
            result = self.exchange.create_order(
                symbol=order.symbol,
                side=order.side.value,
                type=order.order_type.value,
                qty=order.qty,
                price=order.price if order.order_type == OrderType.LIMIT else None,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
            )
            return {"order_id": str(result.get("order_id", "")), "status": "open"}
        except AttributeError:
            logger.error("[executor] Exchange has no create_order method")
            return None

    @property
    def orders(self) -> list[Order]:
        return list(self._orders)

    def cancel(self, symbol: str, order_id: str) -> bool:
        """Отменить ордер (заглушка)."""
        logger.info("[executor] CANCEL %s %s (shadow=%s)", symbol, order_id, self.shadow)
        # TODO: реальная отмена
        return True
