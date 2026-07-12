"""Quick test for Feature Engine imports and basic functionality."""
import asyncio
import sys
sys.path.insert(0, r"G:\bot\crypto-screener-v2")

from core.features.store import FeatureStore, get_feature_store
from core.features.base import BaseFeatureCalculator
from core.features.engine import FeatureEngine, get_feature_engine
from core.features.calculators.whale import WhaleFeatureCalculator
from core.features.calculators.ohlcv import OHLCVFeatureCalculator
from core.features.calculators.indicators import IndicatorsFeatureCalculator
from core.features.calculators.orderbook import OrderBookFeatureCalculator
from core.features.calculators.market import MarketFeatureCalculator
from core.features.calculators.volatility import VolatilityFeatureCalculator


async def test():
    store = get_feature_store()
    print("FeatureStore created:", type(store).__name__)

    await store.set("BTC/USDT:USDT", "test.feature", 42.0, ttl=30)
    val = await store.get("BTC/USDT:USDT", "test.feature")
    print("Set/Get OK:", val)

    result = await store.get_multi(["BTC/USDT:USDT"], ["test.feature"])
    print("Bulk OK:", result)

    print("Store stats:", store.stats())

    # Проверяем создание калькуляторов
    whale = WhaleFeatureCalculator()
    ohlcv = OHLCVFeatureCalculator()
    ind = IndicatorsFeatureCalculator()
    ob = OrderBookFeatureCalculator()
    market = MarketFeatureCalculator()
    vol = VolatilityFeatureCalculator()
    print("All calculators created OK")
    print("Whale features:", whale.feature_names)
    print("OHLCV channels:", ohlcv.event_channels)

    # FeatureEngine
    engine = get_feature_engine()
    engine.register(whale)
    engine.register(ohlcv)
    print("Engine stats:", engine.stats)

    print("\nALL OK")


asyncio.run(test())
