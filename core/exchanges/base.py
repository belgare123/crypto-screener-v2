"""
Базовые типы Data Engine: структуры данных + интерфейс нормализатора.
"""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar


# ── Нормализованные типы данных ──

@dataclass
class TradeData:
    symbol: str
    exchange: str
    side: str                # buy / sell
    price: float
    size: float              # в контрактах/base
    value_usdt: float        # условная стоимость в USDT
    ts: float                # unix ms
    trade_id: str = ""
    is_buyer_maker: bool = False


@dataclass
class CandleData:
    symbol: str
    exchange: str
    timeframe: str           # 1m, 5m, 15m, 1h …
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float = 0.0    # оборот в USDT (quote volume)
    ts: float = 0.0          # unix ms open time
    complete: bool = False   # True = свеча закрыта


@dataclass
class OrderBookData:
    symbol: str
    exchange: str
    bids: list[tuple[float, float]]   # [(price, size), …]
    asks: list[tuple[float, float]]
    ts: float = 0.0
    checksum: int = 0


@dataclass
class LiquidationData:
    symbol: str
    exchange: str
    side: str                # buy / sell
    price: float
    size: float
    value_usdt: float
    ts: float = 0.0
    liq_id: str = ""


@dataclass
class FundingData:
    symbol: str
    exchange: str
    rate: float              # текущая ставка (0.0001 = 0.01%)
    predicted_rate: float = 0.0
    ts: float = 0.0
    next_payment_ts: float = 0.0


@dataclass
class OIData:
    symbol: str
    exchange: str
    oi: float                # открытый интерес в USDT
    oi_change_1h_pct: float = 0.0
    ts: float = 0.0


# ── Универсальное событие (что публикуется в шину) ──

@dataclass
class NormalizedEvent:
    """Событие после нормализации, готовое к публикации в MarketDataBus."""
    channel: str       # data.trade.{sym} | data.candle.{sym}.{tf} | data.ob.{sym} …
    exchange: str
    symbol: str
    data: TradeData | CandleData | OrderBookData | LiquidationData | FundingData | OIData
    ts: float = 0.0

    def to_core_event(self):
        """Конвертировать в core.Event для MarketDataBus."""
        from core import Event as CoreEvent
        return CoreEvent(
            channel=self.channel,
            exchange=self.exchange,
            symbol=self.symbol,
            data=self._serialize(),
            ts=self.ts or time.time() * 1000,
        )

    def _serialize(self) -> dict[str, Any]:
        d = {
            "_type": type(self.data).__name__,
        }
        d.update(self.data.__dict__)
        return d


# ── Интерфейс нормализатора ──

class Normalizer(abc.ABC):
    """Абстрактный нормализатор данных одной биржи.

    Каждый метод принимает сырой dict от WS/REST биржи
    и возвращает список NormalizedEvent (обычно 1, но может быть несколько).
    """

    exchange: ClassVar[str] = ""

    @abc.abstractmethod
    def normalize_trade(self, raw: dict) -> list[NormalizedEvent]:
        ...

    @abc.abstractmethod
    def normalize_candle(self, raw: dict) -> list[NormalizedEvent]:
        ...

    @abc.abstractmethod
    def normalize_orderbook(self, raw: dict) -> list[NormalizedEvent]:
        ...

    def normalize_liquidation(self, raw: dict) -> list[NormalizedEvent]:
        """Опционально — не все биржи дают ликвидации."""
        return []

    def normalize_funding(self, raw: dict) -> list[NormalizedEvent]:
        return []

    def normalize_oi(self, raw: dict) -> list[NormalizedEvent]:
        return []
