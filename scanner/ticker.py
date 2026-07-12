"""Ticker, Liquidation scanners — пишут в core.storage TickerStore/LiquidationStore."""

from __future__ import annotations

import logging

from core import Event, get_bus
from core.storage import get_ticker_store
from scanner import BaseScanner

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Ticker Scanner — 24h объём, цена, изменение
# ──────────────────────────────────────────────


class TickerScanner(BaseScanner):
    name = "ticker"
    channel = "ticker.*"

    def __init__(self):
        super().__init__()

    async def process(self, event: Event):
        # Strangler Fig — core.storage
        mapped = {
            "last_price": event.data.get("lastPrice", 0),
            "volume_24h": event.data.get("volume24h", 0),
            "turnover_24h": event.data.get("turnover24h", event.data.get("quoteVolume", 0)),
            "change_24h": event.data.get("price24hPcnt", 0),
            "high_24h": event.data.get("highPrice24h", 0),
            "low_24h": event.data.get("lowPrice24h", 0),
        }
        await get_ticker_store().put(event.symbol, mapped)


# ──────────────────────────────────────────────
#  Liquidation Scanner
# ──────────────────────────────────────────────


class LiquidationScanner(BaseScanner):
    name = "liquidation"
    channel = "liquidation.*"

    async def process(self, event: Event):
        data = event.data
        items = data if isinstance(data, list) else [data]
        for liq in items:
            if not isinstance(liq, dict):
                continue
            from core.storage import get_liquidation_store

            get_liquidation_store().add(event.symbol, event.exchange, liq)
