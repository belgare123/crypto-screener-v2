"""
Comprehensive test for Feature Engine (V2 — v0.10.0).

V1 signal integration tests removed with signals/ package.
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
    print("✓ FeatureStore get_multi")

    # 3. FeatureEngine with all calculators
    engine = get_feature_engine()
    engine.register(WhaleFeatureCalculator())
    engine.register(OHLCVFeatureCalculator())
    engine.register(IndicatorsFeatureCalculator())
    engine.register(OrderBookFeatureCalculator())
    engine.register(MarketFeatureCalculator())
    engine.register(VolatilityFeatureCalculator())

    assert len(engine._calculators) == 6
    print(f"✓ FeatureEngine: {len(engine._calculators)} calculators registered")

    # 4. Calculator by name
    whale = engine.get_calculator("whale")
    assert whale is not None
    print("✓ get_calculator('whale')")

    # 5. Prepare context
    ctx = engine.prepare_context(symbol="BTC/USDT:USDT")
    assert ctx.get("symbol") == "BTC/USDT:USDT"
    print("✓ prepare_context")

    print("\n✅ All FeatureEngine tests passed.")


if __name__ == "__main__":
    asyncio.run(test())
