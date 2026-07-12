"""
DI Container + Phased Bootstrap для Crypto Screener v2.

Container хранит все компоненты приложения в одном месте и управляет
их жизненным циклом (инициализация → старт → останов).

Фазы инициализации (порядок важен):
  Phase.INFRASTRUCTURE — логирование, сигналы ОС
  Phase.STORAGE       — CandleStore, TickerStore, OBStore, etc.
  Phase.FEATURES      — FeatureEngine, FeatureStore, calculators
  Phase.BATCH_UPDATER — BatchFeatureUpdater (background batch refresh)
  Phase.STATE         — StateEngine, regime/trend/volatility
  Phase.CONTEXT       — ContextEngine (L3)
  Phase.DECISION      — DecisionEngine (adaptive thresholds + SL/TP)
  Phase.STRATEGY      — StrategyEngine (L4)
  Phase.SERVICES      — фоновые тикеры (heatmap, correlation, breadth, etc.)
  Phase.TELEGRAM      — TelegramNotifier, handlers
  Phase.WARMUP        — FeatureEngine warmup (REST)
  Phase.RUN           — запуск и ожидание shutdown
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


class Phase(Enum):
    INFRASTRUCTURE = auto()
    STORAGE = auto()
    FEATURES = auto()
    BATCH_UPDATER = auto()
    STATE = auto()
    CONTEXT = auto()
    DECISION = auto()
    STRATEGY = auto()
    SERVICES = auto()
    TELEGRAM = auto()
    WARMUP = auto()
    RUN = auto()

    def __lt__(self, other):
        if not isinstance(other, Phase):
            return NotImplemented
        order = list(Phase)
        return order.index(self) < order.index(other)


class Container:
    """Контейнер зависимостей — все компоненты приложения в одном месте."""

    def __init__(self):
        self._components: dict[str, Any] = {}
        self._ticker_tasks: list[asyncio.Task] = []
        self._running = False
        self.loop_start_time: float = 0.0
        self._current_phase: Phase | None = None

    # ── Регистрация / получение компонентов ──

    def set(self, name: str, instance: Any) -> None:
        """Зарегистрировать компонент по имени."""
        self._components[name] = instance

    def get(self, name: str, default: Any = None) -> Any:
        """Получить компонент по имени."""
        return self._components.get(name, default)

    def require(self, name: str) -> Any:
        """Получить компонент — или raise, если не найден."""
        val = self._components.get(name)
        if val is None:
            raise KeyError(
                f"Component '{name}' not registered "
                f"(current phase: {self._current_phase})"
            )
        return val

    @property
    def phases_completed(self) -> list[Phase]:
        return list(Phase)[: list(Phase).index(self._current_phase) + 1] if self._current_phase else []

    # ── Фазовая инициализация ──

    async def run_phase(self, phase: Phase, setup: Callable) -> None:
        """Выполнить фазу инициализации."""
        self._current_phase = phase
        logger.info("[bootstrap] Phase %s...", phase.name)
        t0 = time.perf_counter()
        try:
            if asyncio.iscoroutinefunction(setup):
                await setup(self)
            else:
                setup(self)
            elapsed = time.perf_counter() - t0
            logger.info("[bootstrap] Phase %s complete (%.2fs)", phase.name, elapsed)
        except Exception:
            logger.exception("[bootstrap] Phase %s FAILED", phase.name)
            raise

    # ── Фоновые задачи ──

    def create_task(self, coro, name: str = "") -> asyncio.Task:
        """Создать фоновую задачу под управлением контейнера."""
        task = asyncio.create_task(coro, name=name)
        self._ticker_tasks.append(task)
        return task

    async def cancel_all_tasks(self):
        """Отменить все фоновые задачи."""
        for task in self._ticker_tasks:
            task.cancel()
        if self._ticker_tasks:
            await asyncio.gather(*self._ticker_tasks, return_exceptions=True)
        self._ticker_tasks.clear()

    # ── Graceful shutdown ──

    async def shutdown(self, signame: str = "SIGTERM"):
        """Graceful shutdown всех компонентов."""
        logger.info("Shutting down (signal=%s)...", signame)
        await self.cancel_all_tasks()

        # Останавливаем компоненты в обратном порядке
        batch_updater = self.get("batch_updater")
        if batch_updater and hasattr(batch_updater, "stop"):
            try:
                await batch_updater.stop()
            except Exception:
                logger.exception("[shutdown] batch_updater stop error")

        context_engine = self.get("context_engine")
        if context_engine and hasattr(context_engine, "stop"):
            try:
                await context_engine.stop()
            except Exception:
                logger.exception("[shutdown] context_engine stop error")

        notifier = self.get("notifier")
        if notifier and hasattr(notifier, "stop"):
            try:
                await notifier.stop()
            except Exception:
                logger.exception("[shutdown] notifier stop error")

        settings_db = self.get("settings_db")
        if settings_db and hasattr(settings_db, "close"):
            try:
                await settings_db.close()
            except Exception:
                logger.exception("[shutdown] settings_db close error")

        exchange = self.get("exchange")
        if exchange and hasattr(exchange, "stop"):
            try:
                await exchange.stop()
            except Exception:
                logger.exception("[shutdown] exchange stop error")

        logger.info("Shutdown complete.")
