"""
PositionSizer — расчёт размера позиции.

Формула:
    qty = capital × risk_% / (ATR × sl_mult)

где:
    risk_% — % капитала под риск (default 1% = 0.01)
    sl_mult — множитель ATR для SL (default 2)
    ATR — в единицах цены

Дополнительно: min/max qty, step size.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PositionSizer:
    """Расчёт размера позиции."""

    def __init__(self, risk_pct: float = 0.01, sl_mult: float = 2.0,
                 min_qty: float = 0.0, max_qty: float = 1_000_000.0,
                 step_size: float = 0.001):
        """
        Args:
            risk_pct: % капитала под риск (0.01 = 1%)
            sl_mult: множитель ATR для SL
            min_qty: минимальный размер позиции
            max_qty: максимальный размер
            step_size: шаг квантования
        """
        self.risk_pct = risk_pct
        self.sl_mult = sl_mult
        self.min_qty = min_qty
        self.max_qty = max_qty
        self.step_size = step_size

    def calculate(self, capital: float, atr: float, price: float,
                  side: str = "buy") -> float:
        """Рассчитать qty в базовой валюте.

        Args:
            capital: доступный капитал в USDT
            atr: ATR(14) в единицах цены
            price: текущая цена
            side: 'buy' или 'sell'

        Returns:
            qty: размер позиции (с учётом min/max/step)
        """
        if atr <= 0 or price <= 0 or capital <= 0:
            return 0.0

        risk_amount = capital * self.risk_pct  # $ amount под риск
        sl_distance = atr * self.sl_mult       # расстояние до SL в цене

        # qty = риск$ / расстояние_SL
        raw_qty = risk_amount / sl_distance

        # Квантование
        qty = self._quantize(raw_qty)

        # Ограничение
        qty = max(self.min_qty, min(self.max_qty, qty))

        logger.debug(
            "[sizer] %s capital=%.0f atr=%.2f price=%.2f "
            "risk=%.0f sl_dist=%.2f qty=%.4f",
            side, capital, atr, price, risk_amount, sl_distance, qty,
        )
        return qty

    def _quantize(self, value: float) -> float:
        """Округлить до step_size."""
        if self.step_size <= 0:
            return value
        return round(value / self.step_size) * self.step_size

    def risk_value(self, qty: float, atr: float) -> float:
        """Стоимость риска для заданного qty."""
        return qty * atr * self.sl_mult
