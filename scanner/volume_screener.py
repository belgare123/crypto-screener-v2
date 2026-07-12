"""Volume Screener — мониторинг всех монет Bybit с объёмом >= $1M/день.

Запрашивает REST API Bybit, фильтрует USDT-пары с turnover24h >= $1M,
и отправляет отчёт/сигналы.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiohttp

from core import SignalResult

logger = logging.getLogger(__name__)

BYBIT_REST = "https://api.bybit.com"
MIN_TURNOVER = 1_000_000  # $1M
SCAN_INTERVAL = 300  # каждые 5 минут


@dataclass
class TickerRow:
    """Строка тикера из Bybit REST."""

    symbol: str
    last_price: float
    turnover_24h: float
    volume_24h: float
    price_change_24h: float  # %

    @property
    def display(self) -> str:
        """Человеко-читаемое имя символа (BTCUSDT → BTC/USDT)."""
        s = self.symbol
        if s.endswith("USDT") and s != "USDTUSDT":
            return s[:-4] + "/USDT"
        return s

    @property
    def turnover_m(self) -> float:
        return self.turnover_24h / 1_000_000


class VolumeScreener:
    """Сканер всех USDT-монет Bybit по дневному объёму."""

    def __init__(self, min_turnover: float = 5_000_000):
        self.min_turnover = min_turnover
        self._known_symbols: set[str] = set()  # символы, которые уже превышали порог
        self._session: aiohttp.ClientSession | None = None
        self._listeners: list[callable] = []

    def add_listener(self, fn: callable):
        """Подписаться на сигналы (функция принимает TickerRow)."""
        self._listeners.append(fn)

    async def start(self):
        self._session = aiohttp.ClientSession()
        logger.info(
            "VolumeScreener started (min_turnover=$%.0f, interval=%ds)",
            self.min_turnover,
            SCAN_INTERVAL,
        )

    async def stop(self):
        if self._session:
            await self._session.close()
            self._session = None

    async def fetch_all_tickers(self) -> list[TickerRow]:
        """Получить все USDT-тикеры с Bybit."""
        if not self._session:
            return []
        url = f"{BYBIT_REST}/v5/market/tickers?category=linear"
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json()
        except Exception:
            logger.exception("[volume_screener] REST error")
            return []

        if data.get("retCode") != 0:
            logger.warning("[volume_screener] Bybit API error: %s", data.get("retMsg", "unknown"))
            return []

        tickers: list[TickerRow] = []
        for item in (data.get("result", {}) or {}).get("list", []):
            symbol = item.get("symbol", "")
            # Только USDT
            if not symbol.endswith("USDT"):
                continue
            try:
                turnover = float(item.get("turnover24h", 0) or 0)
                volume = float(item.get("volume24h", 0) or 0)
                price = float(item.get("lastPrice", 0) or 0)
                change = float(item.get("price24hPcnt", 0) or 0) * 100  # в %
            except (ValueError, TypeError):
                continue

            tickers.append(TickerRow(
                symbol=symbol,
                last_price=price,
                turnover_24h=turnover,
                volume_24h=volume,
                price_change_24h=round(change, 2),
            ))

        logger.info("[volume_screener] fetched %d USDT tickers", len(tickers))
        return tickers

    async def scan(self) -> list[TickerRow]:
        """Выполнить сканирование — вернуть тикеры, прошедшие фильтр."""
        all_tickers = await self.fetch_all_tickers()
        if not all_tickers:
            return []
        # Фильтр по объёму
        qualifying = [t for t in all_tickers if t.turnover_24h >= self.min_turnover]
        qualifying.sort(key=lambda t: t.turnover_24h, reverse=True)
        return qualifying

    async def warmup(self):
        """Первый прогон — заполняем known_symbols без уведомлений."""
        qualifying = await self.scan()
        self._known_symbols = {t.symbol for t in qualifying}
        logger.info("[volume_screener] warmup: %d coins with volume >= $%.0fM", len(self._known_symbols), self.min_turnover / 1e6)
        return qualifying

    async def tick(self) -> list[TickerRow]:
        """Один цикл сканирования — вызывает слушателей для новых монет.

        Returns:
            Все qualifying монеты за этот цикл.
        """
        qualifying = await self.scan()
        if not qualifying:
            return []

        # Находим новые (ранее не известные)
        qualifying_symbols = {t.symbol for t in qualifying}
        new_symbols = qualifying_symbols - self._known_symbols
        if new_symbols:
            new_tickers = [t for t in qualifying if t.symbol in new_symbols]
            logger.info(
                "[volume_screener] %d new coin(s) crossed $1M volume: %s",
                len(new_tickers),
                ", ".join(f"{t.symbol} (${t.turnover_m:.1f}M)" for t in new_tickers[:10]),
            )
            for listener in self._listeners:
                for t in new_tickers:
                    try:
                        await listener(t)
                    except Exception:
                        logger.exception("[volume_screener] listener error on %s", t.symbol)
        else:
            logger.debug("[volume_screener] no new coins above $1M (known=%d, total=%.0f)",
                         len(self._known_symbols), len(qualifying))

        # Сохраняем все qualifying как известные
        self._known_symbols = qualifying_symbols
        return qualifying

    async def run_loop(self):
        """Бесконечный цикл сканирования."""
        # Первый прогон — без уведомлений
        try:
            await self.warmup()
        except Exception:
            logger.exception("[volume_screener] warmup error")
        await asyncio.sleep(SCAN_INTERVAL)
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("[volume_screener] scan error")
            await asyncio.sleep(SCAN_INTERVAL)
