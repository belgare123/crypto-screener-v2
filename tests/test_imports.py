"""Sanity check — verify all imports work."""

import sys
from pathlib import Path

# Add project root to path
root = Path(__file__).parent.parent
sys.path.insert(0, str(root))


def test_core_imports():
    from core import Event, MarketDataBus, SignalResult, get_bus
    bus = get_bus()
    assert bus is not None
    print("✅ core imports OK")


def test_signals_imports():
    """V1 signals module removed in v0.10.0."""
    pass  # V2 signals flow through core.signal.SignalEngine


def test_strategy_imports():
    from strategies import StrategyEngine
    assert StrategyEngine is not None
    print("✅ strategy imports OK")


def test_scanner_imports():
    from scanner.candles import CandleScanner
    from scanner.trades import TradeScanner
    from scanner.orderbook import OrderBookScanner
    from scanner.ticker import TickerScanner, LiquidationScanner
    from core.storage import get_candle_store, get_ticker_store, get_ob_store, get_liquidation_store, get_whale_tracker
    print("✅ scanner imports OK")


def test_exchange_imports():
    from exchanges.bybit import BybitExchange
    ex = BybitExchange()
    assert ex.name == "bybit"
    print("✅ exchange imports OK")


def test_cache_worker():
    from core.cache import get_cache
    from core.worker import WorkerPool
    from core.scheduler import Scheduler
    cache = get_cache()
    pool = WorkerPool(2)
    sched = Scheduler()
    print("✅ cache/worker/scheduler imports OK")


def test_api_imports():
    from api import app
    assert app.title == "crypto-screener API"
    print("✅ API imports OK")


if __name__ == "__main__":
    test_core_imports()
    test_signals_imports()
    test_strategy_imports()
    test_scanner_imports()
    test_exchange_imports()
    test_cache_worker()
    test_api_imports()
    print("\n🎉 All import tests passed!")
