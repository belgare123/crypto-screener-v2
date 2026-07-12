"""
BaseFeatureCalculator — базовый класс для всех калькуляторов признаков.
Каждый калькулятор:
1. Знает, на какие события шины подписываться (event_channels)
2. Вычисляет один или несколько признаков по требованию
3. Сам решает, когда пересчитываться (на основе TTL и входящих событий)
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from core import Event

logger = logging.getLogger(__name__)


class BaseFeatureCalculator(ABC):
    """
    Абстрактный калькулятор признаков.

    Наследники определяют:
    - event_channels: на какие каналы шины подписываться
    - feature_names: какие фичи производит этот калькулятор
    - default_ttl: TTL по умолчанию для фич
    - compute(): основная логика расчёта

    FeatureEngine вызывает compute() при:
    - Получении события из подписанного канала
    - Прямом запросе из стратегии, если фича протухла
    """

    # Каналы событий, на которые подписан калькулятор
    event_channels: list[str] = []
    """Список каналов вида 'trades.*', 'candles.*', 'orderbook.*' и т.д."""

    # Имена фич, которые производит этот калькулятор
    feature_names: list[str] = []
    """Пример: ['whale.trades', 'whale.cvd', 'whale.summary']"""

    # TTL для каждой фичи (по умолчанию для всех)
    default_ttl: float = 60.0

    # Приоритет: меньше = раньше (0 = первым)
    priority: int = 100

    def __init__(self, feature_store=None):
        from core.features.store import get_feature_store
        self._store = feature_store or get_feature_store()
        self._last_compute: dict[str, float] = {}  # symbol -> timestamp
        self._compute_lock = asyncio.Lock()
        self._compute_count: int = 0

    @abstractmethod
    async def compute(self, symbol: str, event: Event | None = None) -> dict[str, Any]:
        """
        Вычислить признаки для символа.

        Args:
            symbol: тикер (напр. 'BTC/USDT:USDT')
            event: событие, вызвавшее пересчёт (может быть None при forced compute)

        Returns:
            dict[name, value] — словарь признаков для сохранения в FeatureStore.
            Ключи — имена фич из self.feature_names (или любые другие).
        """
        ...

    async def compute_if_expired(self, symbol: str) -> bool:
        """
        Принудительно пересчитать, если фичи протухли.
        Возвращает True, если был пересчёт.

        Вызывается FeatureEngine.get_feature(), если фича протухла.
        """
        # Проверяем, не вычисляли ли недавно
        last = self._last_compute.get(symbol, 0.0)
        if time.time() - last < min(self.default_ttl, 5.0):
            return False

        async with self._compute_lock:
            # Double-check
            last = self._last_compute.get(symbol, 0.0)
            if time.time() - last < min(self.default_ttl, 5.0):
                return False

            features = await self.compute(symbol)
            if features:
                await self._store.set_multi(
                    [(symbol, name, val) for name, val in features.items()],
                    ttl=self.default_ttl,
                )
                self._compute_count += 1
                self._last_compute[symbol] = time.time()
            return True

    async def compute_batch(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        """Вычислить признаки для нескольких символов за один проход.

        Дефолтная реализация — последовательный вызов compute().
        Калькуляторы, поддерживающие векторные операции, переопределяют этот метод.

        Args:
            symbols: список тикеров

        Returns:
            dict: {symbol: {name: value, ...}, ...}
        """
        import asyncio

        result: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            try:
                features = await self.compute(symbol)
                if features:
                    result[symbol] = features
                    self._compute_count += 1
                    self._last_compute[symbol] = time.time()
            except Exception:
                logger.exception(
                    "[feat] calculator '%s' compute_batch error for %s",
                    self.__class__.__name__, symbol,
                )
        return result

    async def on_event(self, event: Event):
        """
        Обработать событие с шины.
        FeatureEngine вызывает это, когда приходит событие из event_channels.
        """
        symbol = event.symbol
        # Проверяем TTL — не пересчитываем чаще чем default_ttl
        last = self._last_compute.get(symbol, 0.0)
        ttl = max(self.default_ttl, 1.0)  # минимум 1сек между вычислениями
        if time.time() - last < ttl:
            return

        # Асинхронно вычисляем (не блокируем шину)
        async with self._compute_lock:
            last = self._last_compute.get(symbol, 0.0)
            if time.time() - last < ttl:
                return

            try:
                features = await self.compute(symbol, event)
            except Exception:
                logger.exception(
                    "[feat] calculator '%s' compute error for %s",
                    self.__class__.__name__, symbol,
                )
                return

            if features:
                await self._store.set_multi(
                    [(symbol, name, val) for name, val in features.items()],
                    ttl=self.default_ttl,
                )
                self._compute_count += 1
                self._last_compute[symbol] = time.time()

    @property
    def compute_count(self) -> int:
        """Сколько раз был вызван compute (мониторинг)."""
        return self._compute_count

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(channels={self.event_channels}, features={self.feature_names})"
