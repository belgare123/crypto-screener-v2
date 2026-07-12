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

ReactiveContextEngine:
- TTL-кэш MarketContext по символу
- Автоинвалидация при обновлении regime.* / vol.* через FeatureStore observers
- Подписка на MarketDataBus для немедленного обновления (опционально)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core import MarketDataBus
from core.features.store import FeatureStore, get_feature_store
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
#  CachedContext — один элемент кэша
# ──────────────────────────────────────────────


class CachedContext:
    """Кэшированный MarketContext с TTL и флагом stale."""

    __slots__ = ("context", "created_at", "expires_at", "stale")

    def __init__(self, context: MarketContext, ttl: float):
        now = time.time()
        self.context = context
        self.created_at = now
        self.expires_at = now + ttl if ttl > 0 else 0.0
        self.stale = False  # помечен устаревшим по событию (FeatureStore observer)

    @property
    def is_valid(self) -> bool:
        """Жив ли кэш: не протух по TTL и не помечен stale."""
        if self.stale:
            return False
        if 0 < self.expires_at < time.time():
            return False
        return True


# ──────────────────────────────────────────────
#  ContextEngine — реактивный контекстный движок
# ──────────────────────────────────────────────


class ContextEngine:
    """
    Контекстный движок с реактивным кэшированием.

    - Кэширует MarketContext по символу (TTL по умолчанию 30 с)
    - Подписывается на FeatureStore observers для regime.* и vol.*
    - Автоматически инвалидирует кэш при обновлении релевантных фич
    - Опционально подписывается на MarketDataBus для событийной инвалидации

    Usage:
        engine = ContextEngine(feature_store, session_engine)
        await engine.start()           # подписка на observers
        ctx = await engine.get_context("BTC/USDT:USDT")
        await engine.stop()            # отписка
    """

    FEATURE_DEPS = [
        "regime.trend",
        "vol.regime",
        "vol.regime_score",
        "regime.volatility_state",
    ]

    def __init__(
        self,
        feature_store: FeatureStore | None = None,
        session_engine=None,
        bus: MarketDataBus | None = None,
        default_ttl: float = 30.0,
    ):
        self._fs = feature_store or get_feature_store()
        self._se = session_engine
        self._bus = bus
        self._default_ttl = default_ttl
        self._cache: dict[str, CachedContext] = {}
        self._lock = asyncio.Lock()
        self._running = False

    # ── Lifecycle ──

    async def start(self):
        """Подписаться на FeatureStore observers."""
        if self._running:
            return
        self._running = True
        self._fs.observe_prefix("regime.", self._on_feature_update)
        self._fs.observe_prefix("vol.", self._on_feature_update)
        logger.info("[ctx] ContextEngine started (TTL=%ss, deps=%s)",
                     self._default_ttl, self.FEATURE_DEPS)

    async def stop(self):
        """Отписаться от observers и очистить кэш."""
        if not self._running:
            return
        self._running = False
        # Prefix observers хранятся с ключом ("*", prefix) в FeatureStore
        self._fs.unobserve("*", "regime.", self._on_feature_update)
        self._fs.unobserve("*", "vol.", self._on_feature_update)
        self._cache.clear()
        logger.info("[ctx] ContextEngine stopped")

    # ── Observer — auto-invalidation ──

    async def _on_feature_update(self, symbol: str, name: str, value: Any):
        """FeatureStore обновил regime.* или vol.* — помечаем кэш stale."""
        if not self._running:
            return
        async with self._lock:
            cached = self._cache.get(symbol)
            if cached and not cached.stale:
                cached.stale = True
                logger.debug("[ctx] invalidated %s due to %s=%s", symbol, name, value)

    # ── Core API ──

    async def get_context(self, symbol: str) -> MarketContext:
        """
        Собрать контекст для символа с кэшированием.

        Если в кэше есть валидный MarketContext — возвращает его.
        Иначе пересобирает из FeatureStore + SessionEngine.
        """
        # Быстрый путь: кэш попадание
        async with self._lock:
            cached = self._cache.get(symbol)
            if cached and cached.is_valid:
                return cached.context

        # Кэш промах или устарел — собираем заново
        ctx = await self._build_context(symbol)

        # Сохраняем в кэш
        async with self._lock:
            self._cache[symbol] = CachedContext(ctx, self._default_ttl)

        return ctx

    async def _build_context(self, symbol: str) -> MarketContext:
        """Собрать MarketContext из FeatureStore + SessionEngine."""
        ctx = MarketContext(symbol=symbol)

        # ── Читаем фичи из FeatureStore ──
        try:
            trend_f = await self._fs.get(symbol, "regime.trend")
            vol_f = await self._fs.get(symbol, "vol.regime")
            score_f = await self._fs.get(symbol, "vol.regime_score")
            vol_state = await self._fs.get(symbol, "regime.volatility_state")
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

        # ── Сессия ──
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

    # ── Cache management ──

    async def invalidate(self, symbol: str | None = None):
        """
        Принудительная инвалидация кэша.

        - Без аргументов: очищает весь кэш
        - С symbol: удаляет только запись для указанного символа
        """
        async with self._lock:
            if symbol:
                self._cache.pop(symbol, None)
            else:
                self._cache.clear()

    @property
    def cache_size(self) -> int:
        """Количество закэшированных контекстов."""
        return len(self._cache)

    async def cache_stats(self) -> dict:
        """Статистика кэша для мониторинга."""
        now = time.time()
        valid = 0
        expired = 0
        stale = 0
        async with self._lock:
            for c in self._cache.values():
                if c.stale:
                    stale += 1
                elif 0 < c.expires_at < now:
                    expired += 1
                else:
                    valid += 1
        return {
            "total": len(self._cache),
            "valid": valid,
            "expired": expired,
            "stale": stale,
            "default_ttl": self._default_ttl,
        }


# ──────────────────────────────────────────────
#  Global singleton (backward compat)
# ──────────────────────────────────────────────

_context_engine: ContextEngine | None = None


def get_context_engine() -> ContextEngine:
    """Глобальный синглтон ContextEngine (backward compat)."""
    global _context_engine
    if _context_engine is None:
        _context_engine = ContextEngine()
    return _context_engine


def reset_context_engine():
    """Сброс синглтона (для тестов)."""
    global _context_engine
    _context_engine = None
