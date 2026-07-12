"""
StateEngine — центральный сборщик состояния рынка.

Агрегирует все детекторы:
1. TrendDetector → TrendDimension
2. NoiseDetector → NoiseDimension
3. ParticipationDetector → ParticipationDimension
4. (Будущие: VolDetector, LiqDetector)

Публикует MarketStateSnapshot в MarketDataBus и FeatureStore.

Режимы:
- shadow: пишет в лог, НЕ блокирует сигналы
- active: сохраняет в FeatureStore, сигналы могут читать
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from typing import Any

from core import Event, MarketDataBus, get_bus
from core.state.market_state import (
    MarketStateSnapshot,
    TrendDimension,
    NoiseDimension,
    ParticipationDimension,
    FEATURE_NAMES,
)
from core.state.trend import TrendDetector
from core.state.noise import NoiseDetector
from core.state.participation import ParticipationDetector

logger = logging.getLogger(__name__)


# ── Интервал обновления ──

DEFAULT_UPDATE_INTERVAL = 30.0  # сек между обновлениями MarketState


class StateEngine:
    """
    State Engine — сборщик состояния рынка.

    Usage:
        engine = StateEngine()
        await engine.start()  # запускает циклическое обновление

        # Чтение состояния
        state = await engine.get_state("BTC/USDT:USDT")
        if state.trend.label == "strong_up":
            ...
    """

    def __init__(
        self,
        bus: MarketDataBus | None = None,
        feature_store=None,
        interval: float = DEFAULT_UPDATE_INTERVAL,
        shadow: bool = True,
    ):
        from core.features.store import get_feature_store

        self.bus = bus or get_bus()
        self._store = feature_store or get_feature_store()
        self.interval = interval
        self.shadow = shadow  # shadow = true → только логи, не влияет на сигналы

        # Детекторы
        self._trend = TrendDetector(feature_store=self._store)
        self._noise = NoiseDetector()
        self._participation = ParticipationDetector(feature_store=self._store)

        # Состояния по символам
        self._states: dict[str, MarketStateSnapshot] = {}
        self._last_update: dict[str, float] = {}

        # Флаг работы
        self._running = False
        self._task: asyncio.Task | None = None

    # ── Registry ──

    @property
    def tracked_symbols(self) -> list[str]:
        """Список отслеживаемых символов."""
        return list(self._states.keys())

    # ── Основной цикл ──

    async def start(self):
        """Запустить циклическое обновление состояний."""
        if self._running:
            logger.warning("[state] engine already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("[state] engine started (interval=%.1fs, shadow=%s)", self.interval, self.shadow)

    async def stop(self):
        """Остановить цикл."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[state] engine stopped")

    async def _loop(self):
        """Фоновый цикл обновления состояний."""
        while self._running:
            try:
                symbols = self._get_active_symbols()
                for symbol in symbols:
                    await self._update_symbol(symbol)

                if not symbols:
                    await asyncio.sleep(2.0)  # тихий цикл, если нет символов
                else:
                    await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("[state] loop error")
                await asyncio.sleep(5.0)

    def _get_active_symbols(self) -> list[str]:
        """Получить список активных символов из шины."""
        # В будущем: читать из списка подписок MarketDataBus
        # Пока: берём то, что уже обновляли
        if not self._states:
            # Дефолтные символы для старта
            return ["BTC/USDT:USDT", "ETH/USDT:USDT"]
        return list(self._states.keys())

    async def _update_symbol(self, symbol: str) -> MarketStateSnapshot | None:
        """Обновить MarketState для одного символа."""
        # Проверяем TTL
        now = time.time()
        last = self._last_update.get(symbol, 0.0)
        if now - last < self.interval:
            return self._states.get(symbol)

        # Параллельно запускаем детекторы
        trend_task = self._trend.detect(symbol)
        noise_task = self._noise.detect(symbol, candles=None)
        part_task = self._participation.detect(symbol)

        trend, noise, participation = await asyncio.gather(
            trend_task, noise_task, part_task,
            return_exceptions=True,
        )

        # Маскируем ошибки
        if isinstance(trend, Exception):
            logger.warning("[state] trend error for %s: %s", symbol, trend)
            trend = TrendDimension()
        if isinstance(noise, Exception):
            logger.warning("[state] noise error for %s: %s", symbol, noise)
            noise = NoiseDimension()
        if isinstance(participation, Exception):
            logger.warning("[state] participation error for %s: %s", symbol, participation)
            participation = ParticipationDimension()

        # Интегральные метрики
        regime = self._compute_regime(trend, noise)
        confidence = self._compute_confidence(trend, noise, participation)

        state = MarketStateSnapshot(
            symbol=symbol,
            timestamp=now,
            exchange="bybit",
            trend=trend,
            noise=noise,
            participation=participation,
            regime=regime,
            confidence=confidence,
            is_ready=True,
        )

        self._states[symbol] = state
        self._last_update[symbol] = now

        # Публикуем в FeatureStore (если не shadow)
        if not self.shadow:
            await self._publish_state(state)

        logger.info(
            "[state] %s → %s vol=%s noise=%s conf=%.0f%%",
            symbol, state.regime,
            state.volatility.label,
            state.noise.label,
            state.confidence,
        )

        return state

    def _compute_regime(
        self,
        trend: TrendDimension,
        noise: NoiseDimension,
    ) -> str:
        """Определить общий рыночный режим."""
        if trend.label in ("strong_up",):
            return "bull"
        elif trend.label in ("strong_down",):
            return "bear"
        elif trend.label == "sideways" and noise.label == "high":
            return "volatile"  # боковик + шум = волатильный
        elif trend.label == "sideways":
            return "ranging"
        elif noise.is_noisy:
            return "volatile"

        return "neutral"

    def _compute_confidence(
        self,
        trend: TrendDimension,
        noise: NoiseDimension,
        participation: ParticipationDimension,
    ) -> float:
        """Общая уверенность в MarketState (0-100)."""
        # Трендовая уверенность
        trend_conf = trend.score / 100.0 if trend.score > 0 else 0.3

        # Шум уменьшает уверенность
        noise_penalty = (100.0 - noise.score) / 100.0

        # Участие: институционал = выше уверенность
        part_conf = participation.score / 100.0

        confidence = (trend_conf * 0.5 + part_conf * 0.2) * noise_penalty
        return round(min(100, max(0, confidence * 100)), 1)

    async def _publish_state(self, state: MarketStateSnapshot):
        """Сохранить MarketState в FeatureStore."""
        try:
            entries = [
                (state.symbol, "state.trend.label", state.trend.label),
                (state.symbol, "state.trend.score", state.trend.score),
                (state.symbol, "state.volatility.label", state.volatility.label),
                (state.symbol, "state.volatility.score", state.volatility.score),
                (state.symbol, "state.liquidity.label", state.liquidity.label),
                (state.symbol, "state.liquidity.score", state.liquidity.score),
                (state.symbol, "state.noise.label", state.noise.label),
                (state.symbol, "state.noise.score", state.noise.score),
                (state.symbol, "state.participation.label", state.participation.label),
                (state.symbol, "state.participation.score", state.participation.score),
                (state.symbol, "state.regime", state.regime),
                (state.symbol, "state.confidence", state.confidence),
            ]
            await self._store.set_multi(entries, ttl=self.interval * 2)
        except Exception:
            logger.exception("[state] publish error for %s", state.symbol)

    # ── Public API ──

    async def get_state(self, symbol: str) -> MarketStateSnapshot | None:
        """Получить последнее состояние для символа."""
        state = self._states.get(symbol)
        if state is None:
            # Пробуем обновить
            state = await self._update_symbol(symbol)
        return state

    async def get_multi(self, symbols: list[str]) -> dict[str, MarketStateSnapshot]:
        """Получить состояния для нескольких символов."""
        results = {}
        for sym in symbols:
            state = await self.get_state(sym)
            if state:
                results[sym] = state
        return results

    async def get_states_snapshot(self) -> dict[str, dict]:
        """Получить все состояния в виде dict (для API)."""
        return {
            sym: st.to_dict()
            for sym, st in self._states.items()
        }

    def stats(self) -> dict:
        """Статистика Engine."""
        return {
            "tracked_symbols": len(self._states),
            "running": self._running,
            "shadow": self.shadow,
            "interval": self.interval,
            "symbols": list(self._states.keys()),
        }


# ── Глобальный экземпляр ──

_state_engine: StateEngine | None = None


def get_state_engine() -> StateEngine:
    """Получить/создать глобальный StateEngine."""
    global _state_engine
    if _state_engine is None:
        _state_engine = StateEngine(shadow=True)
    return _state_engine
