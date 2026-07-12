"""
DataEngine — сборщик нормализаторов + MultiExchangeMerge.

Роли:
1. Подписывается на MarketDataBus на все каналы (wildcard *)
2. Прогоняет сырые данные через нормализатор по exchange
3. Публикует NormalizedEvent обратно в шину
4. Обновляет MultiExchangeMerge для консенсуса цен

Usage:
    engine = DataEngine(bus=get_bus())
    engine.add_exchange('bybit')
    engine.add_exchange('binance')
    engine.add_exchange('okx')
    # engine подписывается на '*' и фильтрует по exchange внутри
"""
from __future__ import annotations

import logging

from core import Event, MarketDataBus, get_bus
from core.exchanges.base import Normalizer, NormalizedEvent
from core.exchanges.merge import MultiExchangeMerge

logger = logging.getLogger(__name__)


class DataEngine:
    """Data Engine — приём сырых данных → нормализация → публикация + мерж."""

    def __init__(
        self,
        bus: MarketDataBus | None = None,
        merge: MultiExchangeMerge | None = None,
    ):
        self._bus = bus or get_bus()
        self._merge = merge or MultiExchangeMerge()
        self._normalizers: dict[str, Normalizer] = {}

    def add_exchange(self, name: str):
        """Добавить нормализатор по имени биржи.

        name: 'bybit', 'binance', 'okx', 'deribit'
        """
        if name in self._normalizers:
            logger.warning("[data] Normalizer %s already registered", name)
            return
        norm = self._instantiate(name)
        if norm is None:
            logger.error("[data] Unknown exchange '%s'", name)
            return
        self._normalizers[name] = norm
        logger.info("[data] Registered normalizer: %s", name)

    def register_normalizer(self, name: str, normalizer: Normalizer):
        """Зарегистрировать готовый экземпляр нормализатора."""
        self._normalizers[name] = normalizer

    def start(self, exchanges: list[str] | None = None):
        """Подписаться на шину для указанных бирж (или всех зарегистрированных).

        Вызывается после регистрации всех нормализаторов.
        """
        targets = exchanges or list(self._normalizers.keys())
        if not targets:
            logger.warning("[data] No normalizers registered — nothing to start")
            return
        self._bus.subscribe("*", self._route)
        logger.info(
            "[data] DataEngine started — %d normalizers: %s",
            len(targets), targets,
        )

    async def _route(self, ev: Event):
        """Роутер: сырой Event → нормализатор → NormalizedEvent + шина + мерж."""
        exchange = ev.exchange
        channel = ev.channel

        norm = self._normalizers.get(exchange)
        if norm is None:
            return  # не наш нормализатор

        raw = ev.data  # должно быть dict
        if not isinstance(raw, dict):
            return

        # Определяем тип по каналу
        normalized: list[NormalizedEvent] = []

        try:
            if channel.startswith("trade"):
                normalized = norm.normalize_trade(raw)
            elif channel.startswith("candle") or channel.startswith("kline"):
                normalized = norm.normalize_candle(raw)
            elif channel.startswith("orderbook") or channel.startswith("depth") or channel.startswith("ob"):
                normalized = norm.normalize_orderbook(raw)
            elif channel.startswith("liquidation") or channel.startswith("liq"):
                normalized = norm.normalize_liquidation(raw)
            elif channel.startswith("funding"):
                normalized = norm.normalize_funding(raw)
            elif channel.startswith("oi"):
                normalized = norm.normalize_oi(raw)
        except Exception:
            logger.exception("[data] Normalizer %s failed on %s", exchange, channel)
            return

        # Публикуем обратно в шину и обновляем мерж
        for nev in normalized:
            await self._bus.publish(nev.to_core_event())
            # Обновляем MultiExchangeMerge для трейдов
            if hasattr(nev.data, "price"):
                self._merge.update_trade(nev.data)

    @staticmethod
    def _instantiate(name: str) -> Normalizer | None:
        if name == "bybit":
            from core.exchanges.bybit_norm import BybitNormalizer
            return BybitNormalizer()
        if name == "binance":
            from core.exchanges.binance_norm import BinanceNormalizer
            return BinanceNormalizer()
        if name == "okx":
            from core.exchanges.okx_norm import OKXNormalizer
            return OKXNormalizer()
        if name == "deribit":
            from core.exchanges.deribit_norm import DeribitNormalizer
            return DeribitNormalizer()
        return None

    @property
    def merge(self) -> MultiExchangeMerge:
        return self._merge
