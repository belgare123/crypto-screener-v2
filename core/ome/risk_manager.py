"""
RiskManager — расчёт SL/TP для позиции.

Логика:
- SL = entry - ATR × sl_mult (для BUY), entry + ATR × sl_mult (для SELL)
- TP = entry + ATR × tp_mult (для BUY), entry - ATR × tp_mult (для SELL)
- Проверка: SL не ближе чем min_distance_pct%

Используется как pre-compute перед отправкой ордера.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class RiskManager:
    """Управление рисками позиции (SL/TP)."""

    def __init__(self, sl_mult: float = 2.0, tp_mult: float = 3.0,
                 min_distance_pct: float = 0.1):
        """
        Args:
            sl_mult: множитель ATR для стоп-лосса
            tp_mult: множитель ATR для тейк-профита
            min_distance_pct: минимальное расстояние SL от entry в % (0.1 = 0.1%)
        """
        self.sl_mult = sl_mult
        self.tp_mult = tp_mult
        self.min_distance_pct = min_distance_pct

    def calculate(self, entry_price: float, atr: float, side: str) -> dict:
        """Рассчитать SL и TP.

        Returns:
            dict с {stop_loss, take_profit, sl_pct, tp_pct, risk_reward_ratio}
        """
        if entry_price <= 0 or atr <= 0:
            return {"stop_loss": 0, "take_profit": 0, "sl_pct": 0, "tp_pct": 0, "risk_reward_ratio": 0}

        sl_distance = atr * self.sl_mult
        tp_distance = atr * self.tp_mult

        if side == "buy":
            stop_loss = entry_price - sl_distance
            take_profit = entry_price + tp_distance
        else:
            stop_loss = entry_price + sl_distance
            take_profit = entry_price - tp_distance

        # Проверка минимального расстояния
        min_dist = entry_price * self.min_distance_pct / 100

        if side == "buy" and stop_loss >= entry_price - min_dist:
            stop_loss = entry_price - min_dist
        elif side == "sell" and stop_loss <= entry_price + min_dist:
            stop_loss = entry_price + min_dist

        sl_pct = abs(entry_price - stop_loss) / entry_price * 100
        tp_pct = abs(entry_price - take_profit) / entry_price * 100
        rrr = tp_pct / sl_pct if sl_pct > 0 else 0

        logger.debug(
            "[risk_mgr] %s entry=%.2f atr=%.2f SL=%.2f TP=%.2f RRR=%.2f",
            side, entry_price, atr, stop_loss, take_profit, rrr,
        )

        return {
            "stop_loss": round(stop_loss, 2),
            "take_profit": round(take_profit, 2),
            "sl_pct": round(sl_pct, 2),
            "tp_pct": round(tp_pct, 2),
            "risk_reward_ratio": round(rrr, 2),
        }
