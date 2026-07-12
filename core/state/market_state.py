"""
MarketState — единая модель состояния рынка.

Собирает все измерения рынка в одну структуру.
Каждое измерение имеет:
- label: человекочитаемый статус
- score: 0-100 числовая оценка
- meta: дополнительные детали

Используется:
- StateEngine: для агрегации
- Signal Engine: как контекст принятия решений
- Risk Engine: для оценки риска
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


# ── State labels ──

TREND_LABELS = ("strong_up", "weak_up", "sideways", "weak_down", "strong_down")
VOLATILITY_LABELS = ("low", "normal", "high", "extreme")
LIQUIDITY_LABELS = ("low", "normal", "high")
NOISE_LABELS = ("low", "normal", "high")
PARTICIPATION_LABELS = ("retail", "balanced", "institutional")


@dataclass
class TrendDimension:
    """Трендовое состояние."""
    label: str = "sideways"       # strong_up / weak_up / sideways / weak_down / strong_down
    score: float = 0.0            # 0-100 (100 = strongest trend)
    ema8: float | None = None
    ema21: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    adx: float | None = None      # сила тренда
    macd: dict | None = None
    slope_5m: float | None = None  # наклон за 5 свечей
    meta: dict = field(default_factory=dict)


@dataclass
class VolatilityDimension:
    """Волатильность."""
    label: str = "normal"         # low / normal / high / extreme
    score: float = 0.0            # 0-100
    atr_pct: float | None = None
    regime_score: float | None = None
    volatility_state: str | None = None  # compression / expansion
    meta: dict = field(default_factory=dict)


@dataclass
class LiquidityDimension:
    """Ликвидность (на основе спреда, depth, OB)."""
    label: str = "normal"         # low / normal / high
    score: float = 50.0           # 0-100
    spread_pct: float | None = None
    depth_bid: float | None = None
    depth_ask: float | None = None
    depth_ratio: float | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class NoiseDimension:
    """Шумность рынка."""
    label: str = "normal"         # low / normal / high
    score: float = 50.0           # 0-100 (100 = noisy)
    noise_pct: float | None = None
    directional_candles: int | None = None
    body_to_wick_ratio: float | None = None
    is_noisy: bool = False
    meta: dict = field(default_factory=dict)


@dataclass
class ParticipationDimension:
    """Тип участников."""
    label: str = "balanced"       # retail / balanced / institutional
    score: float = 50.0           # 0-100 (ближе к 0 = retail, 100 = institutional)
    oi_change_pct: float | None = None
    cvd_trend: str | None = None
    whale_volume_pct: float | None = None
    taker_ratio: float | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class MarketStateSnapshot:
    """
    Полный снэпшот состояния рынка для одного символа в момент времени.
    Легковесный (dataclass) — можно кешировать.
    """
    symbol: str = ""
    timestamp: float = 0.0
    exchange: str = "bybit"

    # Измерения
    trend: TrendDimension = field(default_factory=TrendDimension)
    volatility: VolatilityDimension = field(default_factory=VolatilityDimension)
    liquidity: LiquidityDimension = field(default_factory=LiquidityDimension)
    noise: NoiseDimension = field(default_factory=NoiseDimension)
    participation: ParticipationDimension = field(default_factory=ParticipationDimension)

    # Интегральные метрики
    regime: str = "neutral"       # bull / bear / ranging / volatile
    confidence: float = 0.0       # 0-100 — общая уверенность в оценке
    is_ready: bool = False        # все измерения инициализированы

    def to_dict(self) -> dict:
        """Сериализация для логов/API."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "exchange": self.exchange,
            "trend": {
                "label": self.trend.label,
                "score": self.trend.score,
                "adx": self.trend.adx,
                "meta": self.trend.meta,
            },
            "volatility": {
                "label": self.volatility.label,
                "score": self.volatility.score,
                "regime": self.volatility.meta.get("regime"),
            },
            "liquidity": {
                "label": self.liquidity.label,
                "score": self.liquidity.score,
                "spread_pct": self.liquidity.spread_pct,
            },
            "noise": {
                "label": self.noise.label,
                "score": self.noise.score,
                "is_noisy": self.noise.is_noisy,
                "noise_pct": self.noise.noise_pct,
            },
            "participation": {
                "label": self.participation.label,
                "score": self.participation.score,
            },
            "regime": self.regime,
            "confidence": self.confidence,
            "is_ready": self.is_ready,
        }

    def __repr__(self) -> str:
        return (
            f"MarketState({self.symbol}: "
            f"trend={self.trend.label}({self.trend.score:.0f}) "
            f"vol={self.volatility.label}({self.volatility.score:.0f}) "
            f"noise={self.noise.label}({self.noise.score:.0f}) "
            f"liq={self.liquidity.label}({self.liquidity.score:.0f}) "
            f"part={self.participation.label})"
        )


# ── Названия фич для FeatureStore ──

FEATURE_NAMES = [
    "state.trend.label",
    "state.trend.score",
    "state.volatility.label",
    "state.volatility.score",
    "state.liquidity.label",
    "state.liquidity.score",
    "state.noise.label",
    "state.noise.score",
    "state.participation.label",
    "state.participation.score",
    "state.regime",
    "state.confidence",
]
