"""
Data Engine — унифицированный слой нормализации данных с бирж.

Схема:
1. Normalizer: по одному на биржу (bybit, binance, okx, deribit)
2. MultiExchangeMerge: объединяет потоки по символу, выдаёт консенсус цены
3. DataEngine: публикует нормализованные события в MarketDataBus

Usage:
    engine = DataEngine(bus=get_bus(), exchanges=['bybit', 'binance'])
    await engine.start()  # подключает WS нормализаторы
"""

from __future__ import annotations

from .base import (
    TradeData,
    CandleData,
    OrderBookData,
    LiquidationData,
    FundingData,
    OIData,
    NormalizedEvent,
    Normalizer,
)
from .bybit_norm import BybitNormalizer
from .binance_norm import BinanceNormalizer
from .okx_norm import OKXNormalizer
from .deribit_norm import DeribitNormalizer
from .merge import MultiExchangeMerge, ConsolidatedPrice
from .engine import DataEngine

__all__ = [
    # Типы
    "TradeData", "CandleData", "OrderBookData",
    "LiquidationData", "FundingData", "OIData",
    "NormalizedEvent", "Normalizer",
    # Нормализаторы
    "BybitNormalizer", "BinanceNormalizer",
    "OKXNormalizer", "DeribitNormalizer",
    # Мерж
    "MultiExchangeMerge", "ConsolidatedPrice",
    # Engine
    "DataEngine",
]
