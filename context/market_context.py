"""
MarketContext — срез рыночного состояния для одного символа.
Level 3 в архитектуре ARCHITECTURE_V2.md.

Читает из FeatureStore:
- regime.trend (bull/bear/flat)
- vol.regime (low/normal/high/extreme)
- vol.regime_score (0-100)
- regime.volatility_state (expansion/compression/stable)

Из core/session.py:
- текущая торговая сессия
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from core.session import (
    SessionType,
    get_current_session_type,
    get_session_engine,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  MarketContext — срез контекста
# ──────────────────────────────────────────────


@dataclass
class MarketContext:
    """Текущее состояние рынка для одного символа."""

    symbol: str
    trend: str = "flat"               # bull / bear / flat
    volatility: str = "normal"        # low / normal / high / extreme
    volatility_state: str = "stable"  # expansion / compression / stable
    regime_score: float = 35.0        # 0-100
    session: str = "asia"             # asia / london / ny / overlap_lon_ny / asia_london
    session_name: str = "Asia"        # человеко-читаемое имя
    session_momentum_weight: float = 1.0  # множитель для импульсных сигналов
    threshold_multiplier: float = 1.0     # множитель порогов

    @classmethod
    def default(cls, symbol: str) -> "MarketContext":
        """Контекст по умолчанию — когда данных нет."""
        return cls(symbol=symbol)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "trend": self.trend,
            "volatility": self.volatility,
            "volatility_state": self.volatility_state,
            "regime_score": self.regime_score,
            "session": self.session,
            "session_name": self.session_name,
            "session_momentum_weight": self.session_momentum_weight,
            "threshold_multiplier": self.threshold_multiplier,
        }

    def __repr__(self) -> str:
        return (
            f"MarketContext({self.symbol}: "
            f"trend={self.trend}, vol={self.volatility}, "
            f"session={self.session_name}, bonus={self.session_momentum_weight:.1f}x)"
        )


# ──────────────────────────────────────────────
#  ContextEngine
# ──────────────────────────────────────────────


class ContextEngine:
    """
    Контекстный движок — собирает MarketContext из FeatureStore + SessionEngine.

    Usage:
        engine = ContextEngine(feature_engine)
        ctx = await engine.get_context("BTC/USDT:USDT")
    """

    def __init__(self, feature_engine=None, session_engine=None):
        from core.features import get_feature_engine

        self._fe = feature_engine or get_feature_engine()
        self._se = session_engine  # SessionEngine (опционально, тикается из run.py)

    async def get_context(self, symbol: str) -> MarketContext:
        """Собрать контекст для символа."""
        ctx = MarketContext(symbol=symbol)

        if self._fe is None:
            return ctx

        # ── Читаем фичи из FeatureStore ──
        try:
            trend_f = await self._fe.get_feature(symbol, "regime.trend")
            vol_f = await self._fe.get_feature(symbol, "vol.regime")
            score_f = await self._fe.get_feature(symbol, "vol.regime_score")
            vol_state = await self._fe.get_feature(symbol, "regime.volatility_state")
        except Exception:
            logger.debug("[ctx] no features yet for %s", symbol, exc_info=True)
            trend_f = vol_f = score_f = vol_state = None

        if trend_f is not None:
            ctx.trend = str(trend_f)
        if vol_f is not None:
            ctx.volatility = str(vol_f)
        if score_f is not None:
            ctx.regime_score = float(score_f)
        if vol_state is not None:
            ctx.volatility_state = str(vol_state)

        # ── Сессия (используем переданный SE или синглтон) ──
        try:
            se = self._se or get_session_engine()
            ctx.session = se.current.value
            profile = se.get_profile()
            ctx.session_name = profile.name
            ctx.session_momentum_weight = profile.momentum_weight
            ctx.threshold_multiplier = se.get_threshold_multiplier()
        except Exception:
            # Если SessionEngine не инициализирован — используем detect
            try:
                from core.session import detect_session, utc_hour_now

                s_type = detect_session(utc_hour_now())
                ctx.session = s_type.value
            except Exception:
                pass

        return ctx


# ──────────────────────────────────────────────
#  Global singleton
# ──────────────────────────────────────────────

_context_engine: ContextEngine | None = None


def get_context_engine() -> ContextEngine:
    """Глобальный синглтон ContextEngine."""
    global _context_engine
    if _context_engine is None:
        _context_engine = ContextEngine()
    return _context_engine


def reset_context_engine():
    global _context_engine
    _context_engine = None
