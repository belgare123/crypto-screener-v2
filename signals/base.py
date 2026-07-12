"""
Signal Base — BaseSignal, SignalMeta, register, registry.
Вынесен в отдельный модуль, чтобы избежать циклических импортов.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from core import SignalResult

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Registry — авто-регистрация сигналов
# ──────────────────────────────────────────────

@dataclass
class SignalMeta:
    name: str
    description: str
    category: str         # volume, whale, breakout, smart_money, ...
    default_score: float = 50.0
    cooldown: int = 1800  # 30 min
    enabled: bool = True
    timeframes: list[str] | None = None  # None = все


_signal_registry: dict[str, type["BaseSignal"]] = {}


def register(
    name: str,
    description: str = "",
    category: str = "general",
    default_score: float = 50.0,
    cooldown: int = 1800,
    enabled: bool = True,
    timeframes: list[str] | None = None,
):
    """Декоратор для регистрации сигнала в реестре."""
    def wrapper(cls: type["BaseSignal"]):
        cls.meta = SignalMeta(
            name=name,
            description=description or cls.__doc__ or "",
            category=category,
            default_score=default_score,
            cooldown=cooldown,
            enabled=enabled,
            timeframes=timeframes,
        )
        _signal_registry[name] = cls
        return cls
    return wrapper


def get_signal(name: str) -> type["BaseSignal"] | None:
    return _signal_registry.get(name)


def list_signals() -> dict[str, SignalMeta]:
    return {n: cls.meta for n, cls in _signal_registry.items()}


def get_signals_by_category(category: str) -> list[type["BaseSignal"]]:
    return [cls for cls in _signal_registry.values() if cls.meta.category == category]


# ──────────────────────────────────────────────
#  BaseSignal
# ──────────────────────────────────────────────

@dataclass
class SignalContext:
    """Контекст для проверки сигнала — все данные, которые могут понадобиться."""
    symbol: str
    exchange: str
    candles: list[dict]                    # 1m из CandleBuffer
    candles_5m: list[dict] | None = None   # 5m (опционально)
    candles_15m: list[dict] | None = None  # 15m (опционально)
    ticker: dict | None = None             # из TickerStore
    oi: dict | None = None                 # из OIStore
    funding: dict | None = None            # из FundingStore
    orderbook: Any = None                  # из OrderBookState
    whale_trades: list[dict] | None = None # из WhaleTracker
    cvd: float = 0.0                       # Cumulative Volume Delta
    liquidations: list[dict] | None = None # из LiquidationStore
    # FeatureEngine — опционально; если не None, сигналы могут брать фичи отсюда
    features: Any | None = None            # FeatureEngine instance (lazy import)
    """FeatureEngine для доступа к кэшированным признакам.
       Использование: ctx.features.get_feature('BTC/USDT:USDT', 'rsi.14')
    """


class BaseSignal(ABC):
    """Базовый класс сигнала. Все сигналы наследуются от него."""

    meta: ClassVar[SignalMeta] = SignalMeta("base", "Base signal", category="base")

    @abstractmethod
    async def check(self, ctx: SignalContext) -> SignalResult | None:
        """
        Проверить сигнал. Вернуть SignalResult или None.
        Результат содержит score 0-100.
        """
        ...

    def _result(
        self,
        symbol: str,
        score: float,
        direction: str = "neutral",
        meta: dict | None = None,
    ) -> SignalResult:
        return SignalResult(
            signal_name=self.meta.name,
            symbol=symbol,
            exchange="",
            score=min(100.0, max(0.0, score)),
            direction=direction,
            meta=meta or {},
            ts=time.time(),
            cooldown=self.meta.cooldown,
        )
