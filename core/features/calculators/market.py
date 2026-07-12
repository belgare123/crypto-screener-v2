"""
Market Feature Calculator — фундаментальные рыночные данные.
Подписывается на ticker.*, liquidation.* и вычисляет:
- Funding rate
- Open Interest
- Liquidation stats
- Ticker (24h stats)

Производит фичи:
- ticker.last: dict — последний тикер (24h stats)
- ticker.volume_24h: float
- ticker.change_24h: float
- liq.recent: list[dict] — последние ликвидации
- liq.volume_5m: float — объём ликвидаций за 5 мин
- liq.volume_1h: float — объём ликвидаций за 1 час
- liq.side_ratio: float — соотношение buy/sell ликвидаций
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from core import Event
from core.features.base import BaseFeatureCalculator

logger = logging.getLogger(__name__)


class MarketFeatureCalculator(BaseFeatureCalculator):
    """
    Калькулятор рыночных признаков (ticker, liquidation).
    """

    event_channels = ["ticker.*", "liquidation.*"]
    feature_names = [
        "ticker.last",
        "ticker.volume_24h",
        "ticker.change_24h",
        "ticker.high_24h",
        "ticker.low_24h",
        "liq.recent",
        "liq.volume_5m",
        "liq.volume_1h",
        "liq.side_ratio",
        "liq.count_5m",
    ]
    default_ttl = 5.0  # тикер и ликвидации обновляются часто
    priority = 30

    def __init__(self, feature_store=None):
        super().__init__(feature_store)
        # Текущий тикер: symbol -> dict
        self._tickers: dict[str, dict] = {}
        # История ликвидаций: deque of dict
        self._liquidations: deque = deque(maxlen=2000)
        # Symbol -> последняя ликвидация
        self._last_liq_by_symbol: dict[str, dict] = {}

    async def compute(self, symbol: str, event: Event | None = None) -> dict:
        """Вычислить рыночные фичи для symbol."""
        features = {}

        # Ticker
        ticker = self._tickers.get(symbol)
        if ticker:
            features["ticker.last"] = ticker
            features["ticker.volume_24h"] = ticker.get("volume_24h", 0.0)
            features["ticker.change_24h"] = ticker.get("change_24h", 0.0)
            features["ticker.high_24h"] = ticker.get("high_24h", 0.0)
            features["ticker.low_24h"] = ticker.get("low_24h", 0.0)

        # Ликвидации для symbol
        now = time.time()
        liqs = [l for l in self._liquidations if l.get("symbol") == symbol]

        features["liq.recent"] = liqs[-50:]

        # Объём за 5 мин
        vol_5m = sum(
            l.get("notional", 0) for l in liqs
            if now - l.get("ts", 0) < 300
        )
        features["liq.volume_5m"] = round(vol_5m, 2)

        # Объём за 1 час
        vol_1h = sum(
            l.get("notional", 0) for l in liqs
            if now - l.get("ts", 0) < 3600
        )
        features["liq.volume_1h"] = round(vol_1h, 2)

        # Соотношение buy/sell
        recent = [l for l in liqs if now - l.get("ts", 0) < 3600]
        buy_vol = sum(l.get("notional", 0) for l in recent if l.get("side") == "buy")
        sell_vol = sum(l.get("notional", 0) for l in recent if l.get("side") == "sell")
        total = buy_vol + sell_vol
        features["liq.side_ratio"] = round(buy_vol / sell_vol, 2) if sell_vol > 0 else float("inf")
        features["liq.count_5m"] = sum(
            1 for l in recent if now - l.get("ts", 0) < 300
        )

        return features

    async def on_event(self, event: Event):
        """Обновить данные в зависимости от типа события."""
        if event.channel.startswith("ticker"):
            self._update_ticker(event)
        elif event.channel.startswith("liquidation"):
            self._update_liquidation(event)
        await super().on_event(event)

    def _update_ticker(self, event: Event):
        """Обновить тикер."""
        data = event.data
        existing = self._tickers.get(event.symbol, {})
        self._tickers[event.symbol] = {
            "symbol": event.symbol,
            "last_price": float(data.get("lastPrice", data.get("last_price", existing.get("last_price", 0)))),
            "volume_24h": float(data.get("volume24h", data.get("volume_24h", existing.get("volume_24h", 0)))),
            "turnover_24h": float(data.get("turnover24h", data.get("quoteVolume", existing.get("turnover_24h", 0)))),
            "change_24h": float(data.get("price24hPcnt", data.get("change_24h", existing.get("change_24h", 0)))),
            "high_24h": float(data.get("highPrice24h", data.get("high_24h", existing.get("high_24h", 0)))),
            "low_24h": float(data.get("lowPrice24h", data.get("low_24h", existing.get("low_24h", 0)))),
            "updated_at": time.time(),
        }

    def _update_liquidation(self, event: Event):
        """Обновить ликвидации."""
        data = event.data
        items = data if isinstance(data, list) else [data]
        for liq in items:
            if not isinstance(liq, dict):
                continue
            side = liq.get("S", liq.get("side", "sell")).lower()
            if side in ("s", "sell"):
                side = "sell"
            elif side in ("b", "buy"):
                side = "buy"
            ts_raw = liq.get("T", liq.get("timestamp", time.time()))
            ts = ts_raw / 1000 if isinstance(ts_raw, (int, float)) and ts_raw > 1e10 else ts_raw
            price = float(liq.get("p", liq.get("price", 0)))
            size = float(liq.get("v", liq.get("size", 0)))
            normalized = {
                "symbol": event.symbol,
                "exchange": event.exchange,
                "side": side,
                "price": price,
                "size": size,
                "notional": price * size,
                "ts": ts,
            }
            self._liquidations.append(normalized)
            self._last_liq_by_symbol[event.symbol] = normalized
