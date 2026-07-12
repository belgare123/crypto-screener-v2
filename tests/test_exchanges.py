"""Tests for Data Engine — normalizers and MultiExchangeMerge."""
from __future__ import annotations

import pytest

from core.exchanges import (
    BybitNormalizer, BinanceNormalizer, OKXNormalizer, DeribitNormalizer,
    MultiExchangeMerge,
)


# --- BybitNormalizer ---

class TestBybitNormalizer:
    """Bybit WS messages use topic/data format. Normalizer always returns list."""

    def _trade_msg(self):
        return {
            "topic": "publicTrade.BTCUSDT",
            "data": [{"T": 1700000000000, "S": "Buy", "p": "50000", "v": "0.1", "i": "123"}],
        }

    def _candle_msg(self):
        return {
            "topic": "kline.1m.BTCUSDT",
            "data": [{"start": 1700000000000, "end": 1700000060000,
                      "o": "20000", "h": "20100", "l": "19900",
                      "c": "20050", "v": "1000", "q": "50000000",
                      "confirm": True}],
        }

    def test_normalize_trade(self):
        n = BybitNormalizer()
        events = n.normalize_trade(self._trade_msg())
        assert len(events) == 1
        # Bybit normalizer converts symbol to unified format
        assert "BTC" in events[0].symbol
        assert events[0].exchange == "bybit"

    def test_normalize_candle(self):
        n = BybitNormalizer()
        events = n.normalize_candle(self._candle_msg())
        assert len(events) == 1
        assert "BTC" in events[0].symbol

    def test_exchange_name(self):
        n = BybitNormalizer()
        assert n.exchange == "bybit"

    def test_empty_msg_returns_empty_list(self):
        n = BybitNormalizer()
        events = n.normalize_trade({})
        assert isinstance(events, list)
        assert len(events) == 0

    def test_sell_trade(self):
        n = BybitNormalizer()
        msg = self._trade_msg()
        msg["data"][0]["S"] = "Sell"
        events = n.normalize_trade(msg)
        assert len(events) == 1


# --- BinanceNormalizer ---

class TestBinanceNormalizer:
    """Binance normalizer receives the inner data dict (not the stream envelope)."""

    def _trade_msg(self):
        return {"E": 1700000000000, "s": "BTCUSDT",
                "m": False, "p": "50000", "q": "0.1", "t": 1}

    def test_normalize_trade(self):
        n = BinanceNormalizer()
        events = n.normalize_trade(self._trade_msg())
        assert len(events) == 1
        assert events[0].exchange == "binance"
        assert events[0].symbol == "BTC/USDT:USDT"

    def test_empty_msg_still_creates_event(self):
        """Binance normalizer always creates a trade event (no error on empty)."""
        n = BinanceNormalizer()
        events = n.normalize_trade({})
        assert isinstance(events, list)
        assert len(events) == 1  # creates default trade


# --- OKXNormalizer ---

class TestOKXNormalizer:
    """OKX WS uses arg/data format."""

    def _trade_msg(self):
        return {
            "arg": {"channel": "trades", "instId": "BTC-USDT"},
            "data": [{"ts": "1700000000000", "instId": "BTC-USDT",
                      "side": "buy", "sz": "0.1", "px": "50000",
                      "tradeId": "123"}],
        }

    def test_normalize_trade(self):
        n = OKXNormalizer()
        events = n.normalize_trade(self._trade_msg())
        assert len(events) == 1
        # OKX normalizer converts to unified symbol format
        assert events[0].exchange == "okx"

    def test_empty_msg_returns_empty_list(self):
        n = OKXNormalizer()
        events = n.normalize_trade({})
        assert isinstance(events, list)
        assert len(events) == 0


# --- DeribitNormalizer ---

class TestDeribitNormalizer:
    """Deribit WS uses params/data format."""

    def test_normalize_trade(self):
        n = DeribitNormalizer()
        raw = {
            "params": {
                "channel": "trades.BTC-PERPETUAL.raw",
                "data": [{"trade_seq": 1, "side": "buy",
                          "amount": 0.1, "price": 20000.0}],
            }
        }
        events = n.normalize_trade(raw)
        assert isinstance(events, list)
        assert len(events) >= 1
        if events:
            assert "BTC" in events[0].symbol

    def test_empty_msg_returns_empty_list(self):
        n = DeribitNormalizer()
        events = n.normalize_trade({})
        assert isinstance(events, list)
        assert len(events) == 0


# --- MultiExchangeMerge ---

class TestMultiExchangeMerge:
    def test_update_ticker(self):
        m = MultiExchangeMerge()
        m.update_ticker("BTC/USDT", "bybit", price=20000.0, volume_24h=1000.0)
        exchanges = m.get_all_exchanges("BTC/USDT")
        assert "bybit" in exchanges

    def test_update_ticker_multiple(self):
        m = MultiExchangeMerge()
        m.update_ticker("BTC/USDT", "bybit", price=20000.0, volume_24h=1000.0)
        m.update_ticker("BTC/USDT", "binance", price=20010.0, volume_24h=2000.0)
        exchanges = m.get_all_exchanges("BTC/USDT")
        assert "bybit" in exchanges
        assert "binance" in exchanges

    def test_consolidate(self):
        m = MultiExchangeMerge()
        m.update_ticker("BTC/USDT", "bybit", price=20000.0, volume_24h=1000.0)
        m.update_ticker("BTC/USDT", "binance", price=20010.0, volume_24h=2000.0)
        result = m.consolidate("BTC/USDT")
        assert result is not None
        assert result.median_price > 0
        assert result.vwap_price > 0

    def test_consolidate_no_data(self):
        m = MultiExchangeMerge()
        result = m.consolidate("NONEXISTENT")
        assert result is None

    def test_clear(self):
        m = MultiExchangeMerge()
        m.update_ticker("BTC/USDT", "bybit", price=20000.0)
        assert len(m.get_all_exchanges("BTC/USDT")) > 0
        m.clear()
        assert len(m.get_all_exchanges("BTC/USDT")) == 0
