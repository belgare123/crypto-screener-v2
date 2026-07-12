"""Order Book Scanner — анализ стакана, спуфинг, айсберги, дисбаланс."""

from __future__ import annotations

import time
from collections import defaultdict

from core import Event, get_bus
from core.storage import get_ob_store
from core.storage.ob_store import OrderBookState
from scanner import BaseScanner

# ── Временный глобальный dict (Strangler Fig) ─────────────────
# Порядок удаления: когда все потребители переведены на core.storage,
# этот dict и импорт OrderBookState из scanner удаляются.
orderbooks: dict[str, OrderBookState] = defaultdict(
    lambda: OrderBookState(max_levels=20)
)


class OrderBookScanner(BaseScanner):
    """Слушает orderbook.* и обновляет OrderBookState."""

    name = "orderbook"
    channel = "orderbook.*"

    async def process(self, event: Event):
        data = event.data

        # Bybit: {"s":"BTCUSDT","b":[["price","size"]],"a":[[...]]}
        bids = [[float(p), float(s)] for p, s in data.get("b", [])]
        asks = [[float(p), float(s)] for p, s in data.get("a", [])]

        if bids or asks:
            # Strangler Fig: пишем в ОБА хранилища
            # 1) Старое — для signals/engine.py и api (пока не мигрированы)
            ob = orderbooks[event.symbol]
            ob.update(bids, asks)

            # 2) Новое core.storage — для run.py и будущих потребителей
            await get_ob_store().put_snapshot(event.symbol, bids, asks, time.time())
