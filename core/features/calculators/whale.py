"""
Whale Feature Calculator — крупные трейды, CVD, агрессия.
Заменяет WhaleTracker.trades в контексте сигналов,
предоставляет FeatureEngine-версию whale-данных.

Производит фичи:
- whale.trades: list[dict] — все whale-сделки за последние 60 мин
- whale.summary: dict | None — лучшая сделка
- whale.cvd: float — Cumulative Volume Delta за 60 мин
- whale.aggression: dict — агрессия buy/sell за 5/15/60 мин
- whale.clusters: list[dict] — кластеры крупных сделок
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from core import Event
from core.features.base import BaseFeatureCalculator

logger = logging.getLogger(__name__)


class WhaleFeatureCalculator(BaseFeatureCalculator):
    """
    Калькулятор whale-признаков.
    Подписывается на трейды и вычисляет:
    - Список крупных сделок
    - CVD
    - Агрессию
    - Кластеры
    """

    event_channels = ["trades.*"]
    feature_names = [
        "whale.trades",
        "whale.summary",
        "whale.cvd",
        "whale.aggression",
    ]
    default_ttl = 5.0  # trades live — TTL 5 секунд
    priority = 10  # высокий приоритет (считается до сигналов)

    WHALE_THRESHOLDS = [100_000, 250_000, 500_000, 1_000_000]
    WINDOW_MINUTES = 60

    def __init__(self, feature_store=None):
        super().__init__(feature_store)
        # Деки трейдов: symbol -> deque of trade dicts
        self._trades: dict[str, deque] = defaultdict(lambda: deque(maxlen=20000))
        self._window = self.WINDOW_MINUTES * 60

    async def compute(self, symbol: str, event: Event | None = None) -> dict:
        """
        Вычислить все whale-фичи для symbol.

        При event=None (forced compute) — использует накопленные данные.
        """
        trades = list(self._trades.get(symbol, []))
        cutoff = time.time() - self._window

        # Фильтруем по времени
        recent = [t for t in trades if t.get("ts", 0) > cutoff]
        whales = [t for t in recent if t.get("notional", 0) >= 100_000]

        # CVD
        cvd = 0.0
        for t in recent:
            side = t.get("side", "")
            vol = float(t.get("notional", 0))
            if side == "buy":
                cvd += vol
            elif side == "sell":
                cvd -= vol

        # Summary — лучшая сделка
        best = None
        if whales:
            best = max(whales, key=lambda t: t.get("notional", 0))

        # Агрессия buy/sell за разные окна
        aggression = self._calc_aggression(recent)

        return {
            "whale.trades": whales,
            "whale.summary": best,
            "whale.cvd": cvd,
            "whale.aggression": aggression,
        }

    async def on_event(self, event: Event):
        """
        Обработать трейд — сохранить в буфер, затем вычислить фичи.
        Переопределяем родительский on_event для минимальной задержки:
        сохраняем трейд немедленно, вычисляем в фоне.
        """
        data = event.data
        trades = data if isinstance(data, list) else [data]

        for trade in trades:
            if not isinstance(trade, dict):
                continue
            normalized = self._normalize_trade(event, trade)
            if normalized:
                self._trades[event.symbol].append(normalized)

        # Вызываем родительский on_event (с TTL-контролем)
        await super().on_event(event)

    def _normalize_trade(self, event: Event, trade: dict) -> dict | None:
        """Нормализовать трейд в единый формат."""
        try:
            price = float(trade.get("price", trade.get("p", 0)))
            size = float(trade.get("size", trade.get("v", 0)))
            notional = abs(price * size)
            side = trade.get("side", trade.get("S", "buy")).lower()
            if side in ("b", "buy"):
                side = "buy"
            elif side in ("s", "sell"):
                side = "sell"
            else:
                side = "buy"
            return {
                "symbol": event.symbol,
                "exchange": event.exchange,
                "ts": event.ts / 1000 if event.ts > 1e10 else event.ts,
                "price": price,
                "size": size,
                "notional": notional,
                "side": side,
                "type": trade.get("type", "market"),
            }
        except (ValueError, TypeError):
            return None

    def _calc_aggression(self, trades: list[dict]) -> dict:
        """Рассчитать агрессию buy/sell за разные окна."""
        now = time.time()
        result = {}
        for window_min in (5, 15, 60):
            cutoff = now - window_min * 60
            buy_vol = sum(
                t.get("notional", 0) for t in trades
                if t.get("ts", 0) > cutoff and t.get("side") == "buy"
            )
            sell_vol = sum(
                t.get("notional", 0) for t in trades
                if t.get("ts", 0) > cutoff and t.get("side") == "sell"
            )
            total = buy_vol + sell_vol
            ratio = round(buy_vol / sell_vol, 2) if sell_vol > 0 else float("inf")
            result[f"{window_min}m"] = {
                "buy_vol": round(buy_vol, 2),
                "sell_vol": round(sell_vol, 2),
                "total": round(total, 2),
                "buy_sell_ratio": ratio,
                "buy_pct": round(buy_vol / total * 100, 1) if total > 0 else 50.0,
            }
        return result
