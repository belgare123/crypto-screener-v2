"""
BybitNormalizer — нормализует сырые данные Bybit WebSocket → NormalizedEvent.
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
    FundingData,
)


class BybitNormalizer(Normalizer):
    exchange = "bybit"

    def normalize_trade(self, raw: dict) -> list[NormalizedEvent]:
        """Bybit publicTrade: {'topic':'publicTrade.BTCUSDT','data':[{'T':…,'S':'Buy','p':'50000', …}]}"""
        events = []
        topic = raw.get("topic", "")
        symbol = self._symbol_from_topic(topic)
        for t in raw.get("data", []):
            side = t.get("S", "").lower()  # Buy / Sell
            events.append(
                TradeData(
                    symbol=symbol,
                    exchange=self.exchange,
                    side="buy" if side == "buy" else "sell",
                    price=float(t.get("p", 0)),
                    size=float(t.get("v", 0)),
                    value_usdt=float(t.get("p", 0)) * float(t.get("v", 0)),
                    ts=int(t.get("T", 0)),
                    trade_id=t.get("i", ""),
                    is_buyer_maker=t.get("BT", False),
                ).to_event("data.trade")
            )
        return events

    def normalize_candle(self, raw: dict) -> list[NormalizedEvent]:
        """Bybit kline: {'topic':'kline.5.BTCUSDT','data':[{'start':…,'o':'…','h':'…', …}]}"""
        events = []
        topic = raw.get("topic", "")
        symbol = self._symbol_from_topic(topic)
        tf = self._timeframe_from_topic(topic)
        for c in raw.get("data", []):
            o, h, l_, cl, v = [float(c.get(k, 0)) for k in ("o", "h", "l", "c", "v")]
            turnover = float(c.get("q", 0))  # quote volume
            events.append(
                CandleData(
                    symbol=symbol,
                    exchange=self.exchange,
                    timeframe=tf,
                    open=o, high=h, low=l_, close=cl, volume=v,
                    turnover=turnover,
                    ts=int(c.get("start", 0)),
                    complete=bool(c.get("confirm", False)),
                ).to_event("data.candle")
            )
        return events

    def normalize_orderbook(self, raw: dict) -> list[NormalizedEvent]:
        """Bybit orderbook snapshot/delta."""
        topic = raw.get("topic", "")
        symbol = self._symbol_from_topic(topic)
        data = raw.get("data", raw.get("data", {}))
        if isinstance(data, list):
            data = data[0] if data else {}
        bids = [(float(b[0]), float(b[1])) for b in data.get("b", [])]
        asks = [(float(a[0]), float(a[1])) for a in data.get("a", [])]
        ob = OrderBookData(
            symbol=symbol,
            exchange=self.exchange,
            bids=bids,
            asks=asks,
            ts=int(data.get("ts", 0)),
            checksum=int(data.get("cs", 0)),
        )
        return [ob.to_event("data.ob")]

    def normalize_liquidation(self, raw: dict) -> list[NormalizedEvent]:
        events = []
        topic = raw.get("topic", "")
        symbol = self._symbol_from_topic(topic)
        for liq in raw.get("data", []):
            side = liq.get("S", "Sell").lower()
            events.append(
                LiquidationData(
                    symbol=symbol,
                    exchange=self.exchange,
                    side="buy" if side == "buy" else "sell",
                    price=float(liq.get("p", 0)),
                    size=float(liq.get("v", 0)),
                    value_usdt=abs(float(liq.get("p", 0)) * float(liq.get("v", 0))),
                    ts=int(liq.get("T", 0)),
                    liq_id=liq.get("id", ""),
                ).to_event("data.liq")
            )
        return events

    def normalize_funding(self, raw: dict) -> list[NormalizedEvent]:
        events = []
        topic = raw.get("topic", "")
        symbol = self._symbol_from_topic(topic)
        data = raw.get("data", {})
        if isinstance(data, list):
            data = data[0] if data else {}
        events.append(
            FundingData(
                symbol=symbol,
                exchange=self.exchange,
                rate=float(data.get("fundingRate", 0)),
                predicted_rate=float(data.get("predictedFundingRate", 0)),
                ts=int(data.get("fundingTime", 0)),
            ).to_event("data.funding")
        )
        return events

    # ── Helpers ──

    @staticmethod
    def _symbol_from_topic(topic: str) -> str:
        """Bybit topic → symbol: 'publicTrade.BTCUSDT' → 'BTC/USDT:USDT'"""
        parts = topic.split(".")
        raw = parts[-1] if parts else ""
        if not raw:
            return "UNKNOWN"
        # BTCUSDT → BTC/USDT:USDT
        pair = raw.upper()
        if pair.endswith("USDT"):
            base = pair[:-4] if pair != "USDTUSDT" else "USDT"
            return f"{base}/USDT:USDT"
        if pair.endswith("USD"):
            base = pair[:-3]
            return f"{base}/USD:USD"
        return pair

    @staticmethod
    def _timeframe_from_topic(topic: str) -> str:
        """Bybit kline topic → timeframe: 'kline.5.BTCUSDT' → '5m'"""
        parts = topic.split(".")
        if len(parts) >= 3:
            raw = parts[1]
            # Bybit sends '5' for 5m, '15' for 15m, '60' for 1h, 'D' for 1d, 'W' for 1w
            mapping = {"1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
                       "60": "1h", "120": "2h", "240": "4h", "360": "6h",
                       "720": "12h", "D": "1d", "W": "1w", "M": "1M"}
            return mapping.get(raw, raw)
        return "1m"


# ── Helper: dataclass → NormalizedEvent ──

def _to_event(self, prefix: str) -> NormalizedEvent:
    return NormalizedEvent(
        channel=f"{prefix}.{self.symbol}.{getattr(self, 'timeframe', '')}".rstrip("."),
        exchange=self.exchange,
        symbol=self.symbol,
        data=self,
        ts=self.ts,
    )

# Monkey-patch helper methods onto data classes
for _cls in (TradeData, CandleData, OrderBookData, LiquidationData, FundingData):
    _cls.to_event = _to_event
