"""
DeribitNormalizer — нормализует данные Deribit WebSocket.
"""
from __future__ import annotations

from typing import Any

from core.exchanges.base import (
    Normalizer,
    NormalizedEvent,
    TradeData,
    CandleData,
    OrderBookData,
)


class DeribitNormalizer(Normalizer):
    exchange = "deribit"

    def normalize_trade(self, raw: dict) -> list[NormalizedEvent]:
        """Deribit trades: {'params':{'channel':'trades.BTC-PERPETUAL.raw','data':[{'trade_seq':…,'side':'buy', …}]}}"""
        events = []
        params = raw.get("params", {})
        channel = params.get("channel", "")
        data = params.get("data", [])
        if isinstance(data, dict):
            data = [data]
        sym = self._symbol_from_channel(channel, "")
        for t in data:
            side = t.get("side", "buy")
            price = float(t.get("price", 0))
            amount = float(t.get("amount", 0))
            events.append(
                TradeData(
                    symbol=sym,
                    exchange=self.exchange,
                    side=side,
                    price=price,
                    size=amount,
                    value_usdt=price * amount,
                    ts=int(t.get("timestamp", 0) or t.get("time", 0)),
                    trade_id=str(t.get("trade_seq", "")),
                ).to_event("data.trade")
            )
        return events

    def normalize_candle(self, raw: dict) -> list[NormalizedEvent]:
        """Deribit chart: {'params':{'channel':'chart.BTC-PERPETUAL.1'}, 'data':[{'tick':…,'open':…,…}]}"""
        events = []
        params = raw.get("params", {})
        channel = params.get("channel", "")
        data = params.get("data", [])
        if isinstance(data, dict):
            data = [data]
        tf = self._tf_from_channel(channel)
        sym = self._symbol_from_channel(channel, "PERPETUAL")
        for c in data:
            events.append(
                CandleData(
                    symbol=sym,
                    exchange=self.exchange,
                    timeframe=tf,
                    open=float(c.get("open", 0)),
                    high=float(c.get("high", 0)),
                    low=float(c.get("low", 0)),
                    close=float(c.get("close", 0)),
                    volume=float(c.get("volume", 0)),
                    turnover=float(c.get("volume", 0)) * float(c.get("close", 0)),
                    ts=int(c.get("tick", 0)),
                    complete=True,
                ).to_event("data.candle")
            )
        return events

    def normalize_orderbook(self, raw: dict) -> list[NormalizedEvent]:
        params = raw.get("params", {})
        channel = params.get("channel", "")
        data = params.get("data", {})
        if "bids" not in data and "asks" not in data:
            return []
        bids = [(float(b[0]), float(b[1])) for b in data.get("bids", []) if float(b[1]) > 0]
        asks = [(float(a[0]), float(a[1])) for a in data.get("asks", []) if float(a[1]) > 0]
        sym = self._symbol_from_channel(channel, "PERPETUAL")
        ob = OrderBookData(
            symbol=sym, exchange=self.exchange, bids=bids, asks=asks,
            ts=int(data.get("timestamp", 0) or data.get("time", 0)),
        )
        return [ob.to_event("data.ob")]

    @staticmethod
    def _symbol_from_channel(channel: str, default_instrument: str) -> str:
        parts = channel.split(".")
        raw = parts[1] if len(parts) > 1 else default_instrument
        raw = raw.upper()
        if "PERPETUAL" in raw:
            base = raw.split("-")[0]
            return f"{base}/USD:USD"
        # spot or future
        if "-" in raw:
            base, quote = raw.split("-")[:2]
            return f"{base}/{quote}:{quote}"
        return raw

    @staticmethod
    def _tf_from_channel(channel: str) -> str:
        """chart.BTC-PERPETUAL.1 → '1m', .5 → '5m'"""
        parts = channel.split(".")
        if len(parts) < 3:
            return "1m"
        raw = parts[-1]
        mapping = {"1": "1m", "5": "5m", "15": "15m", "30": "30m",
                   "60": "1h", "120": "2h", "240": "4h", "360": "6h",
                   "720": "12h", "1440": "1d", "10080": "1w"}
        return mapping.get(raw, f"{raw}m")
