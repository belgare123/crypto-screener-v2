"""
Тесты для BatchFeatureUpdater — P4: Unit-of-Work для FeatureEngine.

Проверяем:
  - BatchFeatureUpdater lifecycle (start/stop/flush)
  - compute_batch() default implementation (последовательный)
  - refresh_symbols() в FeatureEngine
  - stale-while-revalidate в get_feature()
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import Event
from core.features.base import BaseFeatureCalculator
from core.features.store import FeatureStore
from core.features.engine import FeatureEngine
from core.features.batch_updater import (
    BatchFeatureUpdater,
    PRIORITY_EVENT,
    PRIORITY_STALE,
)


# ── Helpers ──

class MockCalculator(BaseFeatureCalculator):
    """Калькулятор для тестов — считает фиктивные признаки."""

    event_channels = ["test.*"]
    feature_names = ["mock.value", "mock.ts"]
    default_ttl = 60.0  # 1 мин

    def __init__(self, store=None, fail_on: set | None = None):
        super().__init__(store)
        self.fail_on = fail_on or set()
        self.compute_calls: list[str] = []  # symbol -> сколько раз вызван

    async def compute(self, symbol: str, event: Event | None = None) -> dict:
        if symbol in self.fail_on:
            raise RuntimeError(f"Mock failure for {symbol}")
        self.compute_calls.append(symbol)
        return {
            "mock.value": hash(symbol) % 100,
            "mock.ts": time.time(),
        }


# ── BatchFeatureUpdater Lifecycle ──

class TestBatchUpdaterLifecycle:
    """Запуск / останов / flush."""

    @pytest.mark.asyncio
    async def test_start_stop(self):
        fe = FeatureEngine()
        fe.register(MockCalculator())
        updater = BatchFeatureUpdater(fe, interval=0.5)

        assert updater._task is None
        await updater.start()
        assert updater._task is not None
        assert not updater._task.done()

        await updater.stop()
        assert updater._task is None  # очищен после stop

    @pytest.mark.asyncio
    async def test_schedule_and_flush(self):
        fe = FeatureEngine()
        calc = MockCalculator()
        fe.register(calc)
        updater = BatchFeatureUpdater(fe, interval=10.0)

        await updater.schedule("BTC/USDT:USDT")
        await updater.schedule("ETH/USDT:USDT")
        assert updater.pending_count == 2

        # Принудительный flush
        await updater.force_refresh()
        assert updater.pending_count == 0

        # Калькулятор вызван для обоих символов
        assert set(calc.compute_calls) == {"BTC/USDT:USDT", "ETH/USDT:USDT"}

    @pytest.mark.asyncio
    async def test_loop_flushes_automatically(self):
        fe = FeatureEngine()
        calc = MockCalculator()
        fe.register(calc)
        updater = BatchFeatureUpdater(fe, interval=0.1)

        await updater.start()
        await updater.schedule("BTC/USDT:USDT")
        await asyncio.sleep(0.25)

        # Калькулятор должен быть вызван фоновым циклом
        assert "BTC/USDT:USDT" in calc.compute_calls

        await updater.stop()

    @pytest.mark.asyncio
    async def test_stop_flushes_pending(self):
        fe = FeatureEngine()
        calc = MockCalculator()
        fe.register(calc)
        updater = BatchFeatureUpdater(fe, interval=10.0)

        await updater.schedule("BTC/USDT:USDT")
        await updater.schedule("ETH/USDT:USDT")
        await updater.stop()

        # Stop должен сфлашить оставшиеся
        assert "BTC/USDT:USDT" in calc.compute_calls
        assert "ETH/USDT:USDT" in calc.compute_calls


# ── FeatureEngine.refresh_symbols ──

class TestRefreshSymbols:
    """FeatureEngine.refresh_symbols вызывает compute_batch у калькуляторов."""

    @pytest.mark.asyncio
    async def test_refresh_symbols_calls_compute_batch(self):
        store = FeatureStore()
        fe = FeatureEngine(store=store)
        calc = MockCalculator(store=store)
        fe.register(calc)

        await fe.refresh_symbols(["BTC/USDT:USDT", "ETH/USDT:USDT"])

        assert set(calc.compute_calls) == {"BTC/USDT:USDT", "ETH/USDT:USDT"}

        # Результаты должны быть в FeatureStore
        val = await store.get("BTC/USDT:USDT", "mock.value")
        assert val is not None
        val2 = await store.get("ETH/USDT:USDT", "mock.value")
        assert val2 is not None

    @pytest.mark.asyncio
    async def test_refresh_error_graceful(self):
        """Ошибка в одном калькуляторе не убивает всю пачку."""
        store = FeatureStore()
        fe = FeatureEngine(store=store)
        calc_ok = MockCalculator(store=store)
        calc_fail = MockCalculator(store=store, fail_on={"BTC/USDT:USDT"})
        fe.register(calc_ok)
        fe.register(calc_fail)

        # Не должно упасть
        await fe.refresh_symbols(["BTC/USDT:USDT", "ETH/USDT:USDT"])

        assert "BTC/USDT:USDT" in calc_ok.compute_calls
        assert "ETH/USDT:USDT" in calc_ok.compute_calls


# ── Stale-While-Revalidate ──

class TestStaleWhileRevalidate:
    """get_feature с batch_updater — stale-while-revalidate."""

    @pytest.mark.asyncio
    async def test_stale_value_returned(self):
        store = FeatureStore()
        fe = FeatureEngine(store=store)
        calc = MockCalculator(store=store)
        fe.register(calc)
        updater = BatchFeatureUpdater(fe, interval=10.0)

        # Устанавливаем "протухшее" значение
        await store.set("BTC/USDT:USDT", "mock.value", 42, ttl=0.01)
        await asyncio.sleep(0.02)  # ждём протухания

        # get_feature должен вернуть stale (с batch_updater)
        val = await fe.get_feature("BTC/USDT:USDT", "mock.value", batch_updater=updater)
        assert val == 42  # stale-значение вернулось
        # Символ запланирован на обновление
        assert updater.pending_count >= 1

    @pytest.mark.asyncio
    async def test_fresh_value_no_schedule(self):
        """Свежая фича — возвращаем, не планируем refresh."""
        store = FeatureStore()
        fe = FeatureEngine(store=store)
        calc = MockCalculator(store=store)
        fe.register(calc)
        updater = BatchFeatureUpdater(fe, interval=10.0)

        # Свежее значение
        await store.set("BTC/USDT:USDT", "mock.value", 42, ttl=60)

        val = await fe.get_feature("BTC/USDT:USDT", "mock.value", batch_updater=updater)
        assert val == 42
        assert updater.pending_count == 0  # не должно быть в pending

    @pytest.mark.asyncio
    async def test_no_stale_returns_none(self):
        """Фичи нет — возвращаем None."""
        store = FeatureStore()
        fe = FeatureEngine(store=store)
        fe.register(MockCalculator(store=store))
        updater = BatchFeatureUpdater(fe, interval=10.0)

        val = await fe.get_feature("BTC/USDT:USDT", "mock.value", batch_updater=updater)
        assert val is None  # нет даже stale-значения


# ── compute_batch — дефолтная реализация ──

class TestComputeBatchDefault:
    """BaseFeatureCalculator.compute_batch по умолчанию вызывает compute."""

    @pytest.mark.asyncio
    async def test_default_compute_batch(self):
        calc = MockCalculator()
        results = await calc.compute_batch(["BTC/USDT:USDT", "ETH/USDT:USDT"])
        assert "BTC/USDT:USDT" in results
        assert "ETH/USDT:USDT" in results
        assert "mock.value" in results["BTC/USDT:USDT"]

    @pytest.mark.asyncio
    async def test_default_compute_batch_partial_failure(self):
        calc = MockCalculator(fail_on={"BTC/USDT:USDT"})
        results = await calc.compute_batch(["BTC/USDT:USDT", "ETH/USDT:USDT"])
        assert "BTC/USDT:USDT" not in results  # упал, пропущен
        assert "ETH/USDT:USDT" in results  # успешен


# ── Integration: Updater + Engine + Store ──

class TestIntegration:
    """Полный цикл: schedule → flush → store содержит данные."""

    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        store = FeatureStore()
        fe = FeatureEngine(store=store)
        calc = MockCalculator(store=store)
        fe.register(calc)
        updater = BatchFeatureUpdater(fe, interval=0.1)

        await updater.start()

        # Добавляем символы через schedule
        await updater.schedule("BTC/USDT:USDT")
        await updater.schedule("ETH/USDT:USDT")
        await updater.force_refresh()  # немедленный flush

        # Данные должны быть в store
        btc_val = await store.get("BTC/USDT:USDT", "mock.value")
        eth_val = await store.get("ETH/USDT:USDT", "mock.value")
        assert btc_val is not None
        assert eth_val is not None

        await updater.stop()
