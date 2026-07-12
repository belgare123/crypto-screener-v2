"""Unit tests for core/storage/ — все хранилища."""

import pytest


# ── CandleStore ──────────────────────────────────────────────────────────────


class TestCandleStore:
    @pytest.fixture
    def store(self):
        from core.storage import CandleStore
        return CandleStore(maxlen=5)

    @pytest.fixture
    def sample_candles(self):
        return [
            {"timestamp": 1, "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000},
            {"timestamp": 2, "open": 105, "high": 115, "low": 95, "close": 110, "volume": 1200},
            {"timestamp": 3, "open": 110, "high": 120, "low": 100, "close": 115, "volume": 1500},
        ]

    @pytest.mark.asyncio
    async def test_put_and_get_candles(self, store, sample_candles):
        for c in sample_candles:
            await store.put_candle("BTC/USDT:USDT", "1m", c)

        candles = await store.get_candles("BTC/USDT:USDT", "1m")
        assert len(candles) == 3
        assert candles[-1]["close"] == 115

    @pytest.mark.asyncio
    async def test_get_empty_symbol(self, store):
        assert await store.get_candles("NONEXISTENT", "1m") == []

    @pytest.mark.asyncio
    async def test_latest(self, store, sample_candles):
        for c in sample_candles:
            await store.put_candle("BTC/USDT:USDT", "1m", c)

        latest = await store.get_latest("BTC/USDT:USDT", "1m")
        assert latest is not None
        assert latest["close"] == 115

    @pytest.mark.asyncio
    async def test_latest_empty(self, store):
        assert await store.get_latest("EMPTY", "1m") is None

    @pytest.mark.asyncio
    async def test_limit(self, store, sample_candles):
        for c in sample_candles:
            await store.put_candle("BTC/USDT:USDT", "1m", c)

        candles = await store.get_candles("BTC/USDT:USDT", "1m", limit=2)
        assert len(candles) == 2
        assert candles[0]["close"] == 110

    @pytest.mark.asyncio
    async def test_maxlen(self, store, sample_candles):
        # maxlen=5, добавляем 7 свечей
        for i in range(7):
            await store.put_candle("BTC/USDT:USDT", "1m", {"timestamp": i, "close": i * 10})
        candles = await store.get_candles("BTC/USDT:USDT", "1m")
        assert len(candles) == 5
        assert candles[0]["timestamp"] == 2  # сдвиг на 2

    @pytest.mark.asyncio
    async def test_put_candles_batch(self, store, sample_candles):
        await store.put_candles("BTC/USDT:USDT", "1m", sample_candles)
        candles = await store.get_candles("BTC/USDT:USDT", "1m")
        assert len(candles) == 3

    @pytest.mark.asyncio
    async def test_get_all_intervals(self, store, sample_candles):
        for c in sample_candles:
            await store.put_candle("BTC/USDT:USDT", "1m", c)
        await store.put_candle("BTC/USDT:USDT", "5m", {"timestamp": 1, "close": 105})

        result = await store.get("BTC/USDT:USDT")
        assert result is not None
        assert "1m" in result
        assert "5m" in result

    @pytest.mark.asyncio
    async def test_clear_symbol(self, store, sample_candles):
        for c in sample_candles:
            await store.put_candle("BTC/USDT:USDT", "1m", c)
        await store.clear_symbol("BTC/USDT:USDT")
        assert await store.get_candles("BTC/USDT:USDT", "1m") == []

    @pytest.mark.asyncio
    async def test_delete(self, store, sample_candles):
        for c in sample_candles:
            await store.put_candle("BTC/USDT:USDT", "1m", c)
        await store.delete("BTC/USDT:USDT")
        assert await store.get("BTC/USDT:USDT") is None


# ── TickerStore ──────────────────────────────────────────────────────────────


class TestTickerStore:
    @pytest.fixture
    def store(self):
        from core.storage import TickerStore
        return TickerStore()

    @pytest.mark.asyncio
    async def test_put_and_get(self, store):
        await store.put("BTC/USDT:USDT", {"symbol": "BTC", "price": 50000, "turnover": 1e9})
        ticker = await store.get("BTC/USDT:USDT")
        assert ticker is not None
        assert ticker["price"] == 50000

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store):
        assert await store.get("NONEXISTENT") is None

    @pytest.mark.asyncio
    async def test_all(self, store):
        await store.put("BTC/USDT:USDT", {"symbol": "BTC", "price": 50000, "turnover": 1e9})
        await store.put("ETH/USDT:USDT", {"symbol": "ETH", "price": 3000, "turnover": 5e8})
        all_t = await store.all()
        assert len(all_t) == 2
        assert "BTC/USDT:USDT" in all_t

    @pytest.mark.asyncio
    async def test_get_top_by_turnover(self, store):
        await store.put("A/USDT", {"symbol": "A", "turnover": 100})
        await store.put("B/USDT", {"symbol": "B", "turnover": 300})
        await store.put("C/USDT", {"symbol": "C", "turnover": 200})
        top = await store.get_top("turnover", 2)
        assert len(top) == 2
        assert top[0]["symbol"] == "B"
        assert top[1]["symbol"] == "C"

    @pytest.mark.asyncio
    async def test_get_top_empty(self, store):
        assert await store.get_top("turnover", 5) == []

    @pytest.mark.asyncio
    async def test_delete(self, store):
        await store.put("BTC/USDT:USDT", {"symbol": "BTC"})
        await store.delete("BTC/USDT:USDT")
        assert await store.get("BTC/USDT:USDT") is None


# ── OBStore ──────────────────────────────────────────────────────────────────


class TestOBStore:
    @pytest.fixture
    def store(self):
        from core.storage import OBStore
        return OBStore()

    SAMPLE_BIDS = [(50000.0, 1.5), (49900.0, 2.0)]
    SAMPLE_ASKS = [(50100.0, 1.0), (50200.0, 0.5)]

    @pytest.mark.asyncio
    async def test_put_and_get_snapshot(self, store):
        await store.put_snapshot("BTC/USDT:USDT", self.SAMPLE_BIDS, self.SAMPLE_ASKS, 12345.0)
        snap = await store.get_snapshot("BTC/USDT:USDT")
        assert snap is not None
        assert len(snap.bids) == 2
        assert len(snap.asks) == 2
        assert snap.updated_at == 12345.0

    @pytest.mark.asyncio
    async def test_get_depth(self, store):
        await store.put_snapshot("BTC/USDT:USDT", self.SAMPLE_BIDS, self.SAMPLE_ASKS, 1.0)
        depth = await store.get_depth("BTC/USDT:USDT", 1)
        assert depth is not None
        assert len(depth["bids"]) == 1
        assert depth["bids"][0] == (50000.0, 1.5)

    @pytest.mark.asyncio
    async def test_apply_delta_creates_if_missing(self, store):
        await store.apply_delta("NEW/USDT", [(1.0, 10.0)], [(2.0, 5.0)])
        snap = await store.get_snapshot("NEW/USDT")
        assert snap is not None
        assert len(snap.bids) == 1

    @pytest.mark.asyncio
    async def test_delete(self, store):
        await store.put_snapshot("BTC/USDT:USDT", [], [], 0.0)
        await store.delete("BTC/USDT:USDT")
        assert await store.get("BTC/USDT:USDT") is None


# ── TradeStore ───────────────────────────────────────────────────────────────


class TestTradeStore:
    @pytest.fixture
    def store(self):
        from core.storage import TradeStore
        return TradeStore(maxlen=10)

    @pytest.mark.asyncio
    async def test_put_and_get(self, store):
        trade = {"price": 50000, "size": 0.1, "side": "buy", "timestamp": 1}
        await store.put_trade("BTC/USDT:USDT", trade)
        trades = await store.get_recent("BTC/USDT:USDT", 10)
        assert len(trades) == 1

    @pytest.mark.asyncio
    async def test_get_empty(self, store):
        assert await store.get("NONEXISTENT") is None

    @pytest.mark.asyncio
    async def test_maxlen(self, store):
        for i in range(15):
            await store.put_trade("BTC/USDT:USDT", {"price": i, "timestamp": i})
        trades = await store.get_recent("BTC/USDT:USDT", 100)
        assert len(trades) == 10
        assert trades[0]["price"] == 5  # сдвиг на 5

    @pytest.mark.asyncio
    async def test_clear_symbol(self, store):
        trade = {"price": 50000, "size": 0.1, "side": "buy", "timestamp": 1}
        await store.put_trade("BTC/USDT:USDT", trade)
        await store.clear_symbol("BTC/USDT:USDT")
        assert await store.get_recent("BTC/USDT:USDT", 10) == []

    @pytest.mark.asyncio
    async def test_delete(self, store):
        await store.put_trade("BTC/USDT:USDT", {"price": 50000})
        await store.delete("BTC/USDT:USDT")
        assert await store.get("BTC/USDT:USDT") is None


# ── FeatureStore alias ───────────────────────────────────────────────────────


class TestFeatureStoreAlias:
    def test_import(self):
        """FeatureStore реэкспортируется без ошибок."""
        from core.storage import FeatureStore, get_feature_store
        assert callable(get_feature_store)


# ── Глобальные синглтоны ─────────────────────────────────────────────────────


class TestSingletons:
    def test_getters_return_same_instance(self):
        from core.storage import (
            get_candle_store, get_ticker_store, get_ob_store, get_trade_store,
        )
        assert get_candle_store() is get_candle_store()
        assert get_ticker_store() is get_ticker_store()
        assert get_ob_store() is get_ob_store()
        assert get_trade_store() is get_trade_store()
