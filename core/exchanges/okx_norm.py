"""
OKXNormalizer — нормализует OKX WebSocket данные.
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


class OKXNormalizer(Normalizer):
    exchange = "okx"

    def normalize_trade(self, raw: dict) -> list[NormalizedEvent]:
        """OKX trade: {'arg':{'channel':'trades','instId':'BTC-USDT-SWAP'},'data':[{'side':'buy','px':'50000','sz':'1', …}]}"""
        events = []
        arg = raw.get("arg", {})
        inst_id = arg.get("instId", "")
        for t in raw.get("data", []):
            side = t.get("side", "buy")
            events.append(
                TradeData(
                    symbol=self._symbol(inst_id),
                    exchange=self.exchange,
                    side=side,
                    price=float(t.get("px", 0)),
                    size=float(t.get("sz", 0)),
                    value_usdt=float(t.get("px", 0)) * float(t.get("sz", 0)),
                    ts=int(t.get("ts", 0)),
                    trade_id=t.get("tradeId", ""),
                ).to_event("data.trade")
            )
        return events

    def normalize_candle(self, raw: dict) -> list[NormalizedEvent]:
        """OKX candle: {'arg':{'channel':'candle5m','instId':'BTC-USDT-SWAP'},'data':[['ts','o','h','l','c','vol','volCcy',…]]}"""
        events = []
        arg = raw.get("arg", {})
        inst_id = arg.get("instId", "")
        channel = arg.get("channel", "candle")
        tf = channel.replace("candle", "") or "1m"
        for c in raw.get("data", []):
            o, h, l_, c_, v = [float(c[i]) for i in (1, 2, 3, 4, 5)]
            cc = float(c[7]) if len(c) > 7 else 0  # candletradevol
            events.append(
                CandleData(
                    symbol=self._symbol(inst_id),
                    exchange=self.exchange,
                    timeframe=tf,
                    open=o, high=h, low=l_, close=c_, volume=v,
                    turnover=float(c[6]) if len(c) > 6 else 0,
                    ts=int(c[0]),
                    complete=len(c) > 4,
                ).to_event("data.candle")
            )
        return events

    def normalize_orderbook(self, raw: dict) -> list[NormalizedEvent]:
        """OKX books: {'arg':{'channel':'books','instId':'BTC-USDT-SWAP'},'data':[{'asks':[['px','sz',…]],'bids':[[…]]}]}"""
        arg = raw.get("arg", {})
        inst_id = arg.get("instId", "")
        data = raw.get("data", [{}])
        d = data[0] if data else {}
        bids = [(float(b[0]), float(b[1])) for b in d.get("bids", []) if float(b[1]) > 0]
        asks = [(float(a[0]), float(a[1])) for a in d.get("asks", []) if float(a[1]) > 0]
        ob = OrderBookData(
            symbol=self._symbol(inst_id),
            exchange=self.exchange,
            bids=bids,
            asks=asks,
            ts=int(d.get("ts", 0)),
        )
        return [ob.to_event("data.ob")]

    @staticmethod
    def _symbol(inst_id: str) -> str:
        """BTC-USDT-SWAP → BTC/USDT:USDT, ETH-USDT → ETH/USDT"""
        parts = inst_id.upper().split("-")
        if len(parts) < 2:
            return inst_id
        base, quote = parts[0], parts[1]
        suffix = ":USDT" if quote == "USDT" else f":{quote}"
        return f"{base}/{quote}{suffix}"
