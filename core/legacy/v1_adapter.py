"""
V1 → ContextEngine Adapter.

Единая точка сборки SignalContext для V1 сигналов.
Заменяет 6 прямых вызовов get_candle_store(), get_ticker_store() и т.д.
в _build_context() на один injectable адаптер.

Может работать в двух режимах:
  1. classic — читает напрямую из core.storage (как сейчас, но через адаптер)
  2. feature-first — использует FeatureEngine + ContextEngine где возможно

После v0.7.0 V1 сигналы переходят на feature-first.
После v0.10.0 V1 удалён.
"""

from __future__ import annotations

import logging
from typing import Any

from core import SignalResult
from signals.base import SignalContext

logger = logging.getLogger(__name__)


class V1ContextAdapter:
    """
    Адаптер, собирающий SignalContext для V1 сигналов.

    Принимает ссылки на хранилища и FeatureEngine в конструкторе
    (инверсия зависимостей), а не через глобальные get_*().
    """

    def __init__(
        self,
        candle_store=None,
        ticker_store=None,
        ob_store=None,
        whale_tracker=None,
        liquidation_store=None,
        feature_engine=None,
        context_engine=None,
    ):
        self._cs = candle_store
        self._ts = ticker_store
        self._ob = ob_store
        self._wt = whale_tracker
        self._ls = liquidation_store
        self._fe = feature_engine
        self._ce = context_engine

    async def build_context(
        self,
        symbol: str,
        exchange: str,
    ) -> SignalContext:
        """Собрать SignalContext для одного символа."""
        candles_1m = self._get_candles(symbol, "1", 60)
        candles_5m = self._get_candles(symbol, "5", 30)
        candles_15m = self._get_candles(symbol, "15", 30)
        ticker = self._get_ticker(symbol)
        ob = self._get_ob(symbol)
        whales = self._get_whales(symbol)
        cvd = self._get_cvd(symbol)
        liqs = self._get_liquidations()

        ctx = SignalContext(
            symbol=symbol,
            exchange=exchange,
            candles=candles_1m or [],
            candles_5m=candles_5m,
            candles_15m=candles_15m,
            ticker=ticker,
            orderbook=ob,
            whale_trades=whales,
            cvd=cvd,
            liquidations=liqs,
            features=self._fe,  # FeatureEngine (опционально)
        )
        return ctx

    # ── Приватные методы с fallback ──

    def _get_candles(self, symbol: str, tf: str, limit: int) -> list[dict] | None:
        if self._cs is None:
            logger.warning("[v1-adapter] candle_store not set, returning None for %s/%s", symbol, tf)
            return None
        try:
            return self._cs.get_sync(symbol, tf, limit)
        except Exception:
            logger.debug("[v1-adapter] get_sync(%s, %s) failed", symbol, tf, exc_info=True)
            return None

    def _get_ticker(self, symbol: str) -> dict | None:
        if self._ts is None:
            return None
        try:
            return self._ts.get_sync(symbol)
        except Exception:
            return None

    def _get_ob(self, symbol: str) -> Any | None:
        if self._ob is None:
            return None
        try:
            return self._ob.get_sync(symbol)
        except Exception:
            return None

    def _get_whales(self, symbol: str) -> list[dict] | None:
        if self._wt is None:
            return None
        try:
            return self._wt.get_whales(symbol, threshold=100_000)
        except Exception:
            return None

    def _get_cvd(self, symbol: str) -> float:
        if self._wt is None:
            return 0.0
        try:
            return self._wt.get_cvd(symbol)
        except Exception:
            return 0.0

    def _get_liquidations(self) -> list[dict] | None:
        if self._ls is None:
            return None
        try:
            return self._ls.recent(minutes=5)
        except Exception:
            return None
