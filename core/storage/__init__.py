"""
Хранилища данных для V2-архитектуры.

Заменяют scanner-буферы. Все хранилища in-memory:
  - CandleStore  → deque[maxlen=200] на {symbol → {interval → deque}}
  - TickerStore  → dict[symbol] = ticker
  - OBStore      → dict[symbol] = snapshot {bids, asks, timestamp}
  - TradeStore   → deque[maxlen=1000] на {symbol → deque[trade]}

FeatureStore — реэкспорт из core.features.store (без дублирования).
"""

from .candle_store import CandleStore, CandleDict
from .ticker_store import TickerStore, TickerDict
from .ob_store import OBStore, OrderBookState, OBSnapshot, PriceLevel
from .trade_store import TradeStore
from .feature_store import FeatureStore, get_feature_store
from .liquidation_store import LiquidationStore, get_liquidation_store
from .whale_tracker import WhaleTracker, get_whale_tracker

# Глобальные синглтоны (thread-safe в asyncio)
_candle_store: CandleStore = None  # type: ignore[assignment]
_ticker_store: TickerStore = None  # type: ignore[assignment]
_ob_store: OBStore = None  # type: ignore[assignment]
_trade_store: TradeStore = None  # type: ignore[assignment]


def get_candle_store() -> CandleStore:
    global _candle_store
    if _candle_store is None:
        _candle_store = CandleStore()
    return _candle_store


def get_ticker_store() -> TickerStore:
    global _ticker_store
    if _ticker_store is None:
        _ticker_store = TickerStore()
    return _ticker_store


def get_ob_store() -> OBStore:
    global _ob_store
    if _ob_store is None:
        _ob_store = OBStore()
    return _ob_store


def get_trade_store() -> TradeStore:
    global _trade_store
    if _trade_store is None:
        _trade_store = TradeStore()
    return _trade_store


__all__ = [
    "CandleDict",
    "CandleStore",
    "TickerDict",
    "TickerStore",
    "OBStore",
    "OrderBookState",
    "OBSnapshot",
    "PriceLevel",
    "TradeStore",
    "WhaleTracker",
    "LiquidationStore",
    "FeatureStore",
    "get_feature_store",
    "get_candle_store",
    "get_ticker_store",
    "get_ob_store",
    "get_trade_store",
    "get_whale_tracker",
    "get_liquidation_store",
]
