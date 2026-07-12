"""Trade Scanner — подписывается на трейды и пишет в core.storage WhaleTracker."""

from __future__ import annotations

import logging

from core import Event, get_bus
from scanner import BaseScanner

logger = logging.getLogger(__name__)


class TradeScanner(BaseScanner):
    """Слушает трейды, отправляет в core.storage WhaleTracker."""

    name = "trades"
    channel = "trades.*"

    async def process(self, event: Event):
        data = event.data
        # Bybit publicTrade: data — список трейдов [{...}, ...] или один dict
        trades = data if isinstance(data, list) else [data]
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            price = float(trade.get("price", trade.get("p", 0)))
            size = float(trade.get("size", trade.get("v", 0)))
            notional = abs(price * size)
            side = trade.get("side", trade.get("S", "buy")).lower()
            # Bybit: "Buy"/"Sell" → "buy"/"sell"
            if side in ("buy", "sell"):
                pass
            elif side in ("b", "buy"):
                side = "buy"
            elif side in ("s", "sell"):
                side = "sell"
            else:
                side = "buy"
            normalized = {
                "symbol": event.symbol,
                "exchange": event.exchange,
                "timestamp": event.ts / 1000,
                "price": price,
                "size": size,
                "notional": notional,
                "side": side,
                "type": trade.get("type", "market"),
            }
            # Strangler Fig — core.storage
            from core.storage import get_whale_tracker

            get_whale_tracker().add_trade(event.symbol, normalized)
