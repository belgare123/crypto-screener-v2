"""Order Book Scanner — анализ стакана, спуфинг, айсберги, дисбаланс."""

from __future__ import annotations

import time

from core import Event, get_bus
from core.storage import get_ob_store
from scanner import BaseScanner


class OrderBookScanner(BaseScanner):
    """Слушает orderbook.* и пишет snapshot в core.storage OBStore."""

    name = "orderbook"
    channel = "orderbook.*"

    async def process(self, event: Event):
        data = event.data

        # Bybit: {"s":"BTCUSDT","b":[["price","size"]],"a":[[...]]}
        bids = [[float(p), float(s)] for p, s in data.get("b", [])]
        asks = [[float(p), float(s)] for p, s in data.get("a", [])]

        if bids or asks:
            await get_ob_store().put_snapshot(event.symbol, bids, asks, time.time())
