"""
Candle Scanner — буферизует свечи для индикаторов и сигналов.
Подписывается на 'candles.*' и пишет в core.storage CandleStore.
"""

from __future__ import annotations

import logging
import time

from core import Event, get_bus
from core.storage import get_candle_store
from scanner import BaseScanner

logger = logging.getLogger(__name__)


class CandleScanner(BaseScanner):
    """Слушает 'candles.*' и пишет в CandleStore (core.storage)."""

    name = "candles"
    channel = "candles.*"

    async def process(self, event: Event):
        parts = event.channel.split(".")
        # Формат канала: candles.1m.BTC/USDT:USDT (с tf) или candles.BTC/USDT:USDT (без tf)
        if len(parts) >= 3 and parts[1].endswith("m"):
            tf = parts[1].replace("m", "")  # "1m" -> "1", "5m" -> "5", "15m" -> "15"
        else:
            tf = "1"  # fallback для старого формата candles.SYMBOL

        candle = event.data
        # Bybit kline data может быть списком
        if isinstance(candle, list):
            candle = candle[0] if candle else {}
        # Нормализация полей Bybit → стандарт
        if "v" in candle or "t" in candle:
            candle = {
                "timestamp": candle.get("t", candle.get("timestamp", time.time() * 1000)) / 1000 if isinstance(candle.get("t"), (int, float)) else candle.get("timestamp", time.time()),
                "open": float(candle.get("o", candle.get("open", 0))),
                "high": float(candle.get("h", candle.get("high", 0))),
                "low": float(candle.get("l", candle.get("low", 0))),
                "close": float(candle.get("c", candle.get("close", 0))),
                "volume": float(candle.get("v", candle.get("volume", 0))),
                "quote_volume": float(candle.get("qv", candle.get("quote_volume", 0))),
            }
        try:
            await get_candle_store().put_candle(event.symbol, tf, candle)
        except Exception:
            logger.exception("candle update failed for %s", event.symbol)
