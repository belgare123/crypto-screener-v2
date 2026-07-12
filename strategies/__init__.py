"""
Strategy Engine (L4) — центральный движок стратегий.

Содержит:
- Реестр стратегий (аналог _signal_registry)
- Декоратор @register_strategy
- StrategyEngine — подписывается на шину и запускает стратегии

Dual-Run: работает параллельно с V1 Signal Engine. V1 не отключается.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any

from core import Event
from strategies.base import BaseStrategy, StrategyContext, StrategyMeta, StrategyResult

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Registry
# ──────────────────────────────────────────────

_strategy_registry: dict[str, type[BaseStrategy]] = {}


def register_strategy(
    name: str,
    description: str = "",
    category: str = "general",
    min_score: float = 40.0,
    cooldown: int = 180,
    enabled: bool = True,
    timeframes: list[str] | None = None,
):
    """Декоратор для регистрации стратегии в реестре."""
    def wrapper(cls: type[BaseStrategy]):
        cls.meta = StrategyMeta(
            name=name,
            description=description or cls.__doc__ or "",
            category=category,
            min_score=min_score,
            cooldown=cooldown,
            enabled=enabled,
            timeframes=timeframes,
        )
        _strategy_registry[name] = cls
        return cls
    return wrapper


def get_strategy(name: str) -> type[BaseStrategy] | None:
    return _strategy_registry.get(name)


def list_strategies() -> dict[str, StrategyMeta]:
    return {n: cls.meta for n, cls in _strategy_registry.items()}


# ──────────────────────────────────────────────
#  StrategyEngine — диспетчер стратегий
# ──────────────────────────────────────────────


class StrategyEngine:
    """
    Движок стратегий.

    - Подписывается на события шины (через ручной вызов on_event)
    - Диспетчеризирует события по стратегиям
    - Управляет cooldown
    - Отправляет результаты в V2 Dispatcher

    Usage:
        engine = StrategyEngine(fe, ctx_engine, dispatcher)
        await engine.register_all()
        bus.subscribe("candles.*", engine.on_event)
        bus.subscribe("trades.*", engine.on_event)
    """

    def __init__(
        self,
        feature_engine=None,
        context_engine=None,
        signal_engine=None,  # SignalEngine для push_signal
    ):
        from core.features import get_feature_engine
        from context import get_context_engine

        self._fe = feature_engine or get_feature_engine()
        self._ce = context_engine or get_context_engine()
        self._signal_engine = signal_engine  # SignalEngine (опционально)

        self._strategies: list[BaseStrategy] = []
        self._strategy_by_name: dict[str, BaseStrategy] = {}
        self._running = False

    # ── Registry ──

    def register_all(self):
        """Зарегистрировать все стратегии из реестра."""
        for name, cls in _strategy_registry.items():
            instance = cls()
            self._strategies.append(instance)
            self._strategy_by_name[name] = instance
            logger.info("[strategy] registered '%s' (%s, cd=%ds)", name, cls.meta.category, cls.meta.cooldown)
        logger.info("[strategy] %d strategies loaded", len(self._strategies))

    def get_strategy(self, name: str) -> BaseStrategy | None:
        return self._strategy_by_name.get(name)

    # ── Start / Stop ──

    async def start(self):
        self._running = True
        logger.info("[strategy] Engine started: %d strategies", len(self._strategies))

    async def stop(self):
        self._running = False
        logger.info("[strategy] Engine stopped")

    # ── Event processing ──

    async def on_event(self, event: Event):
        """Обработать событие — проверить все стратегии для символа."""
        if not self._running or not self._strategies:
            return

        symbol = event.symbol
        if not symbol:
            return

        # Собираем контекст один раз для всех стратегий
        try:
            market_context = await self._ce.get_context(symbol)
        except Exception:
            logger.debug("[strategy] context error for %s", symbol, exc_info=True)
            market_context = None

        if market_context is None:
            return

        # Проверяем каждую стратегию
        for strategy in self._strategies:
            if not strategy.meta.enabled:
                continue

            try:
                ctx = StrategyContext(
                    symbol=symbol,
                    features=self._fe,
                    context=market_context,
                    exchange="bybit",
                )
                result = await strategy.evaluate(ctx)
            except Exception:
                logger.exception("[strategy] '%s' crashed on %s", strategy.meta.name, symbol)
                continue

            if result is None:
                continue

            # Cooldown + degrade mode
            if not strategy.can_send(result.symbol, result.score, result.direction):
                continue

            logger.info(
                "[strategy] %s %s score=%.0f dir=%s factors=%s ctx=%s",
                result.strategy_name,
                result.symbol,
                result.score,
                result.direction,
                [f["name"] for f in result.factors],
                result.context.session_name,
            )

            # Отправка в SignalEngine (через push_signal)
            if self._signal_engine is not None:
                try:
                    from core import SignalResult as SR
                    sig = SR(
                        signal_name=result.strategy_name,
                        symbol=result.symbol,
                        exchange="bybit",
                        score=result.score,
                        direction=result.direction,
                        meta={
                            "confidence": result.confidence,
                            "factors": result.factors,
                            "context": result.context.to_dict(),
                            **result.meta,
                        },
                        ts=result.ts,
                        cooldown=strategy.meta.cooldown,
                    )
                    await self._signal_engine.push_signal(sig)
                    logger.info(
                        "[strategy] dispatched %s %s score=%.0f dir=%s → SignalEngine",
                        result.strategy_name, result.symbol, result.score, result.direction,
                    )
                except Exception:
                    logger.exception("[strategy] push_signal error for %s", result.strategy_name)

    # ── Stats ──

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "strategies": len(self._strategies),
            "names": list(self._strategy_by_name.keys()),
            "running": self._running,
        }
