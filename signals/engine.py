"""Signal Engine — принимает события, гоняет сигналы, отдаёт результаты."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict

from core import Event, MarketDataBus, SignalResult, get_bus
from core.adaptive import get_volatility_tracker
from scanner.candles import candle_buffer, CandleBuffer
from scanner.ticker import ticker_store, liquidation_store
from scanner.trades import whale_tracker
from scanner.orderbook import orderbooks
from signals import (
    BaseSignal,
    SignalContext,
    list_signals,
    get_signal,
    _signal_registry,
)
from utils import SignalCooldown
from core.monitoring.registry import get_metrics_registry

logger = logging.getLogger(__name__)

# Metrics shortcuts
_metrics = get_metrics_registry()


class SignalEngine:
    """
    Движок сигналов.
    Подписывается на MarketDataBus, собирает контекст и запускает сигналы.
    """

    def __init__(
        self,
        bus: MarketDataBus | None = None,
        worker_pool=None,
        min_score: float = 40.0,
    ):
        self.bus = bus or get_bus()
        self._signals: list[BaseSignal] = []
        self._min_score = min_score
        self._cooldown = SignalCooldown(default_cooldown=1800)
        self._worker_pool = worker_pool
        self._running = False
        self._signal_queue: asyncio.Queue[SignalResult] = asyncio.Queue()

    def load_signals(self, signal_names: list[str] | None = None):
        """Загрузить сигналы (все или только указанные)."""
        all_signals = list_signals()
        for name, meta in all_signals.items():
            if signal_names and name not in signal_names:
                continue
            if not meta.enabled:
                continue
            cls = _signal_registry[name]
            self._signals.append(cls())
            logger.info("Loaded signal: %s (%s)", name, meta.category)

    async def start(self):
        self._running = True
        self.bus.subscribe("*", self._on_event)
        logger.info(
            "SignalEngine started with %d signals, min_score=%.0f",
            len(self._signals),
            self._min_score,
        )

    async def stop(self):
        self._running = False

    async def _on_event(self, event: Event):
        if not self._running:
            return

        # Debug: считаем ивенты
        if event.channel.startswith("candles.") or event.channel.startswith("trades."):
            if not hasattr(self, "_ev_cnt"):
                self._ev_cnt = 0
            self._ev_cnt += 1
            if self._ev_cnt % 100 == 0:
                logger.info("[engine] processed %d events (%s %s)", self._ev_cnt, event.channel, event.symbol)

        # Нас интересуют только свечи и трейды (основные триггеры)
        if not event.channel.startswith("candles.") and not event.channel.startswith("trades."):
            return

        # Каждые N ms проверяем сигналы для этой монеты
        symbol = event.symbol
        exchange = event.exchange

        # Собираем контекст
        context = await self._build_context(symbol, exchange)

        if not context.candles:
            if len(getattr(self, "_empty_candles_logged", [])) < 10:
                logger.debug("[engine] no candles yet for %s", symbol)
                self._empty_candles_logged = getattr(self, "_empty_candles_logged", []) + [symbol]
            return

        # Запускаем сигналы
        for signal in self._signals:
            await self._check_signal(signal, context)

    async def _build_context(self, symbol: str, exchange: str) -> SignalContext:
        """Собрать все доступные данные для символа."""
        candles_1m = candle_buffer.get(symbol, "1", 60)
        candles_5m = candle_buffer.get(symbol, "5", 30)
        candles_15m = candle_buffer.get(symbol, "15", 30)

        # Берём тикер из stores
        ticker = ticker_store.get(symbol)
        ob = orderbooks.get(symbol)
        whales = whale_tracker.get_whales(symbol, threshold=100_000)
        cvd = whale_tracker.get_cvd(symbol)
        liqs = liquidation_store.recent(minutes=5)

        # Обновляем VolatilityTracker для Adaptive Thresholds
        price = 0.0
        if ticker:
            raw = ticker.get("last_price", 0) or ticker.get("close", 0) or 0
            price = float(raw) if raw else 0.0
        elif candles_1m:
            price = float(candles_1m[-1].get("close", 0) or 0)
        vol_tracker = get_volatility_tracker()
        vol_tracker.update(symbol, candles_1m, candles_5m or None, candles_15m or None, price)

        # FeatureEngine — lazy import
        try:
            from core.features.engine import get_feature_engine
            fe = get_feature_engine()
        except ImportError:
            fe = None

        return SignalContext(
            symbol=symbol,
            exchange=exchange,
            candles=candles_1m,
            candles_5m=candles_5m or None,
            candles_15m=candles_15m or None,
            ticker=ticker,
            oi=None,
            funding=None,
            orderbook=ob,
            whale_trades=whales if whales else None,
            cvd=cvd,
            liquidations=liqs if liqs else None,
            features=fe,  # FeatureEngine — сигналы могут брать признаки отсюда
        )

    async def _check_signal(self, signal: BaseSignal, ctx: SignalContext):
        """Проверить один сигнал, пропустить через антиспам."""
        try:
            result = await signal.check(ctx)
        except Exception:
            logger.exception("Signal '%s' crashed on %s", signal.meta.name, ctx.symbol)
            _metrics.inc("signal_errors", labels={"signal": signal.meta.name, "symbol": ctx.symbol})
            return

        if result is None:
            return

        if not hasattr(self, "_check_cnt"):
            self._check_cnt = 0
        self._check_cnt += 1
        if self._check_cnt <= 3:
            logger.info("[engine] signal %s for %s score=%.0f", signal.meta.name, ctx.symbol, result.score)

        # Фильтр min_score
        if result.score < self._min_score:
            _metrics.inc("signals_blocked", labels={"signal": signal.meta.name, "reason": "min_score"})
            return

        # Антиспам
        if not self._cooldown.can_send(
            result.signal_name,
            result.symbol,
            result.score,
            cooldown=signal.meta.cooldown,
        ):
            _metrics.inc("signals_blocked", labels={"signal": signal.meta.name, "reason": "cooldown"})
            return

        _metrics.inc("signals_total", labels={"signal": signal.meta.name, "symbol": ctx.symbol})

        await self._signal_queue.put(result)

    async def push_signal(self, result: SignalResult):
        """Внешний сигнал напрямую в очередь отправки + DNA коллекция."""
        if result.score < self._min_score:
            return

        await self._signal_queue.put(result)

    async def get_signal(self) -> SignalResult | None:
        """Получить следующий сигнал (блокирующий)."""
        try:
            return await asyncio.wait_for(self._signal_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            return None

    def flush_signals(self) -> list[SignalResult]:
        """Слить все накопившиеся сигналы (non-blocking)."""
        results = []
        while not self._signal_queue.empty():
            try:
                results.append(self._signal_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return results


# singleton
_engine: SignalEngine | None = None


def get_engine() -> SignalEngine:
    global _engine
    if _engine is None:
        _engine = SignalEngine()
    return _engine
