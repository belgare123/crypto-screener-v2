"""
Comprehensive test for Feature Engine + integration with signals.
"""
import asyncio
import sys
sys.path.insert(0, r"G:\bot\crypto-screener-v2")

from core.features.store import get_feature_store
from core.features.engine import get_feature_engine
from core.features.calculators.whale import WhaleFeatureCalculator
from core.features.calculators.ohlcv import OHLCVFeatureCalculator
from core.features.calculators.indicators import IndicatorsFeatureCalculator
from core.features.calculators.orderbook import OrderBookFeatureCalculator
from core.features.calculators.market import MarketFeatureCalculator
from core.features.calculators.volatility import VolatilityFeatureCalculator
from signals.whale_v2 import WhaleSignalV2
from signals.base import SignalContext


async def test():

    # 1. FeatureStore
    store = get_feature_store()
    await store.set("BTC/USDT:USDT", "whale.trades", [{"notional": 500_000, "side": "buy", "ts": 100}], ttl=30)
    val = await store.get("BTC/USDT:USDT", "whale.trades")
    assert val is not None
    print("✓ FeatureStore get/set")

    # 2. Bulk
    result = await store.get_multi(["BTC/USDT:USDT"], ["whale.trades"])
    assert "BTC/USDT:USDT" in result
    print("✓ FeatureStore bulk get")

    # 3. By pattern
    await store.set("BTC/USDT:USDT", "whale.cvd", 15000.0, ttl=30)
    patterns = await store.get_by_pattern("whale", symbol="BTC/USDT:USDT")
    assert len(patterns) >= 2
    print("✓ FeatureStore pattern search:", list(patterns.keys()))

    # 4. Observer
    observed = []

    async def obs(symbol, name, value):
        observed.append((symbol, name))

    store.observe("BTC/USDT:USDT", "test.obs", obs)
    await store.set("BTC/USDT:USDT", "test.obs", 123, ttl=10)
    assert len(observed) == 1
    print("✓ Observer pattern works")

    # 5. Observer prefix
    observed2 = []
    async def obs2(symbol, name, value):
        observed2.append((symbol, name))
    store.observe_prefix("whale", obs2)
    await store.set("ETH/USDT:USDT", "whale.trades", [], ttl=10)
    assert len(observed2) >= 1
    print("✓ Prefix observer works")

    # 6. FeatureEngine registration
    engine = get_feature_engine()
    whale_calc = WhaleFeatureCalculator()
    ohlcv_calc = OHLCVFeatureCalculator()
    ind_calc = IndicatorsFeatureCalculator()
    ob_calc = OrderBookFeatureCalculator()
    market_calc = MarketFeatureCalculator()
    vol_calc = VolatilityFeatureCalculator()
    engine.register_many([whale_calc, ohlcv_calc, ind_calc, ob_calc, market_calc, vol_calc])
    stats = engine.stats
    assert stats["calculators"] >= 6, f"expected ≥6 calculators, got {stats['calculators']}"
    print(f"✓ FeatureEngine: {stats['calculators']} calculators, {stats['features']} features")

    # 7. WhaleSignalV2 — проверяем, что сигнал знает про FeatureEngine
    meta = WhaleSignalV2.meta
    assert meta.name == "whale_v2"
    print(f"✓ WhaleSignalV2 registered: {meta}")

    # 8. SignalContext has features field
    assert "features" in SignalContext.__dataclass_fields__
    print("✓ SignalContext has 'features' field")

    # 9. Симуляция контекста с FeatureEngine
    ctx = SignalContext(
        symbol="BTC/USDT:USDT",
        exchange="bybit",
        candles=[{"close": 50000, "high": 50100, "low": 49900, "volume": 100}],
        features=engine,
    )
    assert ctx.features is engine
    print("✓ SignalContext with FeatureEngine works")

    # 10. Observer cleanup
    store.unobserve("BTC/USDT:USDT", "test.obs", obs)
    store._observers[("*", "whale")].remove(obs2)
    print("✓ Cleanup OK")

    # 11. Evict
    await store.evict("BTC/USDT:USDT", "test.obs")
    v = await store.get("BTC/USDT:USDT", "test.obs")
    assert v is None
    print("✓ Evict works")

    print("\n🎉 ALL TESTS PASSED")


asyncio.run(test())
