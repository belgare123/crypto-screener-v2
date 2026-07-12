"""
FeatureEngine — центральный двигатель признаков.
- Подписывается на MarketDataBus
- Диспетчеризирует события по FeatureCalculator'ам
- Предоставляет стратегиям единый async API: get_feature(), get_multi()
- Управляет жизненным циклом калькуляторов
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

from core import Event, MarketDataBus, get_bus
from core.features.base import BaseFeatureCalculator
from core.features.store import FeatureStore, get_feature_store

logger = logging.getLogger(__name__)


class FeatureEngine:
    """
    Feature Engine.

    Usage:
        engine = FeatureEngine()
        engine.register(WhaleCalculator())
        await engine.start()

        # В стратегии:
        whale_trades = await engine.get_feature("BTC/USDT:USDT", "whale.trades")
        features = await engine.get_multi(
            symbols=["BTC/USDT:USDT", "ETH/USDT:USDT"],
            names=["rsi.14", "ema.50", "atr.14"],
        )
    """

    def __init__(
        self,
        bus: MarketDataBus | None = None,
        store: FeatureStore | None = None,
    ):
        self.bus = bus or get_bus()
        self.store = store or get_feature_store()
        self._calculators: list[BaseFeatureCalculator] = []
        self._calc_by_channel: dict[str, list[BaseFeatureCalculator]] = defaultdict(list)
        self._calc_by_feature: dict[str, BaseFeatureCalculator] = {}
        self._running = False

    # ── Registry ──

    def register(self, calculator: BaseFeatureCalculator):
        """Зарегистрировать калькулятор."""
        self._calculators.append(calculator)
        for channel in calculator.event_channels:
            self._calc_by_channel[channel].append(calculator)
        for name in calculator.feature_names:
            self._calc_by_feature[name] = calculator
        logger.info(
            "[feat] registered %s (channels=%s, features=%s)",
            calculator.__class__.__name__,
            calculator.event_channels,
            calculator.feature_names,
        )

    def register_many(self, calculators: list[BaseFeatureCalculator]):
        for c in calculators:
            self.register(c)

    def get_calculator(self, name: str) -> BaseFeatureCalculator | None:
        """Получить калькулятор по имени класса."""
        for c in self._calculators:
            if c.__class__.__name__ == name:
                return c
        return None

    # ── Start / Stop ──

    async def start(self):
        """Подписаться на шину и запустить калькуляторы."""
        if self._running:
            return
        self._running = True

        # Подписываемся на каждый канал, на который есть калькуляторы
        for channel in self._calc_by_channel:
            self.bus.subscribe(channel, self._on_event)

        logger.info(
            "[feat] FeatureEngine started: %d calculators, %d channels",
            len(self._calculators),
            len(self._calc_by_channel),
        )

    async def stop(self):
        if not self._running:
            return
        self._running = False
        for channel in self._calc_by_channel:
            self.bus.unsubscribe(channel, self._on_event)
        logger.info("[feat] FeatureEngine stopped")

    # ── Event routing ──

    async def _on_event(self, event: Event):
        if not self._running:
            return

        # Точное совпадение
        handlers = self._calc_by_channel.get(event.channel, [])

        # Wildcard: каналы вида 'prefix.*'
        for pattern, calcs in list(self._calc_by_channel.items()):
            if pattern.endswith(".*"):
                prefix = pattern[:-2]
                if event.channel == prefix or event.channel.startswith(prefix + "."):
                    handlers = calcs + handlers

        # Глобальный '*'
        global_calcs = self._calc_by_channel.get("*", [])
        handlers = global_calcs + handlers

        # Дедуплицируем
        seen: set[int] = set()
        unique: list[BaseFeatureCalculator] = []
        for c in handlers:
            if id(c) not in seen:
                seen.add(id(c))
                unique.append(c)

        # Диспетчеризуем по приоритету
        unique.sort(key=lambda c: c.priority)

        for calc in unique:
            try:
                await calc.on_event(event)
            except Exception:
                logger.exception(
                    "[feat] calculator '%s' crashed on %s",
                    calc.__class__.__name__, event.channel,
                )

    # ── Public API для стратегий ──

    async def get_feature(
        self,
        symbol: str,
        name: str,
        auto_compute: bool = True,
        batch_updater=None,
    ) -> Any | None:
        """
        Получить значение признака.

        Режим stale-while-revalidate:
          — если фича есть (даже протухшая) → возвращаем значение и
            планируем фоновый refresh через batch_updater
          — если фичи нет → возвращаем None

        Args:
            symbol: тикер (напр. 'BTC/USDT:USDT')
            name: имя признака (напр. 'rsi.14', 'whale.trades')
            auto_compute: если True — принудительно пересчитать при протухании
                          (фоновый refresh, не блокирующий)
            batch_updater: BatchFeatureUpdater для фонового refresh

        Returns:
            значение признака или None, если недоступен
        """
        # Получаем значение со stale-поддержкой
        value = await self.store.get_stale(symbol, name)
        if value is None:
            # Фичи никогда не было
            if not auto_compute:
                return None
            if batch_updater is not None:
                await batch_updater.schedule(symbol, priority=1)
            return None

        # Проверяем свежесть
        entry = await self.store.get_raw(symbol, name)
        if entry is not None and not entry.is_expired:
            return value  # свежее

        # Фича протухла, но есть stale-значение
        if auto_compute and batch_updater is not None:
            await batch_updater.schedule(symbol, priority=1)
        return value

    async def get_multi(
        self,
        symbols: list[str],
        names: list[str],
        auto_compute: bool = True,
    ) -> dict[str, dict[str, Any]]:
        """
        Получить несколько признаков для нескольких символов.

        Returns:
            {symbol: {name: value, ...}, ...}
        """
        result = await self.store.get_multi(symbols, names)

        if auto_compute:
            # Найти недостающие фичи и пересчитать
            for symbol in symbols:
                for name in names:
                    if result.get(symbol, {}).get(name) is not None:
                        continue
                    calc = self._calc_by_feature.get(name)
                    if calc is None:
                        continue
                    await calc.compute_if_expired(symbol)
                    # Повторная попытка
                    val = await self.store.get(symbol, name)
                    if val is not None:
                        result.setdefault(symbol, {})[name] = val

        return result

    async def get_feature_raw(
        self,
        symbol: str,
        name: str,
    ) -> Any | None:
        """
        Получить "сырое" значение — без auto_compute.
        Полезно для проверки, есть ли фича в кэше без триггера вычисления.
        """
        return await self.store.get(symbol, name)

    async def get_by_pattern(
        self,
        name_prefix: str,
        symbol: str | None = None,
    ) -> dict[str, Any]:
        """Получить все фичи с префиксом (из кэша, без пересчёта)."""
        return await self.store.get_by_pattern(name_prefix, symbol)

    # ── Batch refresh ──

    async def refresh_symbols(self, symbols: list[str]):
        """Обновить признаки для списка символов через batch-методы
        калькуляторов.

        Вызывается BatchFeatureUpdater._flush().
        Каждый калькулятор получает полный список символов → compute_batch()
        → атомарная запись через store.set_multi().

        Args:
            symbols: список тикеров для обновления
        """
        for calc in self._calculators:
            try:
                results = await calc.compute_batch(symbols)
                if not results:
                    continue
                # Собираем все (symbol, name, value) для batch-записи
                items: list[tuple[str, str, Any]] = []
                for symbol, features in results.items():
                    for name, value in features.items():
                        items.append((symbol, name, value))
                if items:
                    await self.store.set_multi(items, ttl=calc.default_ttl)
                    logger.debug(
                        "[feat] refresh_symbols: %s wrote %d values for %d symbols",
                        calc.__class__.__name__, len(items), len(results),
                    )
            except Exception:
                logger.exception(
                    "[feat] calculator '%s' refresh_symbols error",
                    calc.__class__.__name__,
                )

    # ── Observer API (делегировано FeatureStore) ──

    def observe(self, symbol: str, name: str, observer):
        """Подписаться на изменения фичи."""
        self.store.observe(symbol, name, observer)

    def observe_prefix(self, name_prefix: str, observer):
        """Подписаться на все фичи с префиксом."""
        self.store.observe_prefix(name_prefix, observer)

    def unobserve(self, symbol: str, name: str, observer):
        self.store.unobserve(symbol, name, observer)

    # ── Management ──

    async def invalidate(self, symbol: str, name: str | None = None):
        """Принудительно сбросить кэш фичи/символа."""
        await self.store.evict(symbol, name)

    @property
    def stats(self) -> dict:
        """Статистика работы FeatureEngine."""
        return {
            "calculators": len(self._calculators),
            "channels": len(self._calc_by_channel),
            "features": len(self._calc_by_feature),
            "store": self.store.stats(),
            "compute_counts": {
                c.__class__.__name__: c.compute_count for c in self._calculators
            },
        }


# ──────────────────────────────────────────────
#  Sentinel
# ──────────────────────────────────────────────

_NO_DEFAULT = object()


# ──────────────────────────────────────────────
#  Global singleton
# ──────────────────────────────────────────────

_engine: FeatureEngine | None = None


def get_feature_engine() -> FeatureEngine:
    global _engine
    if _engine is None:
        _engine = FeatureEngine()
    return _engine


def reset_feature_engine():
    global _engine
    _engine = None
