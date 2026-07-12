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
    from signals import BaseSignal, list_signals, register
    signals = list_signals()
    assert "volume_spike" in signals
    assert "whale" in signals
    assert "smart_money" in signals
    print(f"✅ signals imports OK ({len(signals)} signals)")


def test_scanner_imports():
    from scanner.candles import candle_buffer, CandleScanner
    from scanner.trades import whale_tracker, TradeScanner
    from scanner.orderbook import orderbooks, OrderBookScanner
    from scanner.ticker import ticker_store, liquidation_store
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
    test_scanner_imports()
    test_exchange_imports()
    test_cache_worker()
    test_api_imports()
    print("\n🎉 All import tests passed!")
