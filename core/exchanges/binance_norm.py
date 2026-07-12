"""
BinanceNormalizer — нормализует данные Binance WebSocket.
"""
from __future__ import annotations

from typing import Any

from core.exchanges.base import (
    Normalizer,
    NormalizedEvent,
    TradeData,
    CandleData,
    OrderBookData,
    LiquidationData,
)


class BinanceNormalizer(Normalizer):
    exchange = "binance"

    def normalize_trade(self, raw: dict) -> list[NormalizedEvent]:
        """Binance trade: {'e':'trade','E':123,'s':'BTCUSDT','p':'50000','q':'1', …}"""
        s = raw.get("s", "")
        is_buyer_maker = raw.get("m", True)
        td = TradeData(
            symbol=self._symbol(s),
            exchange=self.exchange,
            side="sell" if is_buyer_maker else "buy",
            price=float(raw.get("p", 0)),
            size=float(raw.get("q", 0)),
            value_usdt=float(raw.get("p", 0)) * float(raw.get("q", 0)),
            ts=int(raw.get("E", 0)),
            trade_id=raw.get("t", ""),
            is_buyer_maker=is_buyer_maker,
        )
        return [td.to_event("data.trade")]

    def normalize_candle(self, raw: dict) -> list[NormalizedEvent]:
        """Binance kline: {'k':{'t':…,'o':'…','c':'…'}}"""
        k = raw.get("k", {})
        s = k.get("s", raw.get("s", ""))
        o, h, l_, c, v = [float(k.get(k_, 0)) for k_ in ("o", "h", "l", "c", "v")]
        qv = float(k.get("q", 0))
        cd = CandleData(
            symbol=self._symbol(s),
            exchange=self.exchange,
            timeframe=self._map_tf(k.get("i", "1m")),
            open=o, high=h, low=l_, close=c, volume=v,
            turnover=qv,
            ts=int(k.get("t", 0)),
            complete=k.get("x", False),
        )
        return [cd.to_event("data.candle")]

    def normalize_orderbook(self, raw: dict) -> list[NormalizedEvent]:
        """Binance depth: {'lastUpdateId':…,'bids':[['p','q'],…],'asks':[…]}
           или partial: {'e':'depthUpdate', …}"""
        s = raw.get("s", raw.get("symbol", ""))
        bids = [(float(b[0]), float(b[1])) for b in raw.get("bids", []) if float(b[1]) > 0]
        asks = [(float(a[0]), float(a[1])) for a in raw.get("asks", []) if float(a[1]) > 0]
        ob = OrderBookData(
            symbol=self._symbol(s),
            exchange=self.exchange,
            bids=bids,
            asks=asks,
            ts=int(raw.get("E", 0) or raw.get("lastUpdateId", 0)),
        )
        return [ob.to_event("data.ob")]

    def normalize_liquidation(self, raw: dict) -> list[NormalizedEvent]:
        """Binance forceOrder: {'o':{'s':'BTCUSDT','S':'SELL','p':'…','q':'…', …}}"""
        o = raw.get("o", raw)
        s = o.get("s", "")
        side_str = o.get("S", "SELL").lower()
        ld = LiquidationData(
            symbol=self._symbol(s),
            exchange=self.exchange,
            side="buy" if side_str == "buy" else "sell",
            price=float(o.get("p", 0)),
            size=float(o.get("q", 0)),
            value_usdt=float(o.get("p", 0)) * float(o.get("q", 0)),
            ts=int(o.get("T", 0) or o.get("E", 0)),
        )
        return [ld.to_event("data.liq")]

    # ── Helpers ──

    @staticmethod
    def _symbol(s: str) -> str:
        s = s.upper()
        if s.endswith("USDT"):
            return f"{s[:-4]}/USDT:USDT"
        if s.endswith("BUSD"):
            return f"{s[:-4]}/BUSD:BUSD"
        return s

    @staticmethod
    def _map_tf(interval: str) -> str:
        """Binance interval '1m'→'1m', '15m'→'15m', '1h'→'1h', etc."""
        return interval  # Binance uses same notation
