"""Candle Scanner — буферизует свечи для индикаторов и сигналов."""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from core import Event, get_bus
from core.storage import get_candle_store
from scanner import BaseScanner

logger = logging.getLogger(__name__)


class CandleBuffer:
    """
    Хранит последние N свечей для каждого символа + таймфрейма.
    Сигналы берут данные отсюда, а не напрямую из WebSocket.
    """

    def __init__(self, maxlen: int = 200):
        self._buffers: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=maxlen))

    def update(self, symbol: str, timeframe: str, candle: dict):
        key = (symbol, timeframe)
        buf = self._buffers[key]
        # проверка дубликатов
        if buf and buf[-1].get("timestamp") == candle.get("timestamp"):
            buf[-1] = candle
        else:
            buf.append(candle)

    def get(self, symbol: str, timeframe: str, count: int = 50) -> list[dict]:
        key = (symbol, timeframe)
        buf = self._buffers.get(key)
        if not buf:
            return []
        return list(buf)[-count:]

    def get_last(self, symbol: str, timeframe: str) -> dict | None:
        key = (symbol, timeframe)
        buf = self._buffers.get(key)
        if buf:
            return buf[-1]
        return None

    def clear(self, symbol: str | None = None, timeframe: str | None = None):
        if symbol and timeframe:
            self._buffers.pop((symbol, timeframe), None)
        elif symbol:
            keys = [k for k in self._buffers if k[0] == symbol]
            for k in keys:
                self._buffers.pop(k, None)
        else:
            self._buffers.clear()

    @property
    def size(self) -> int:
        return sum(len(b) for b in self._buffers.values())


# Глобальный буфер свечей
candle_buffer = CandleBuffer(maxlen=200)


class CandleScanner(BaseScanner):
    """Слушает 'candles.*' и пишет в CandleBuffer."""

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
            # Strangler Fig: пишем в оба хранилища
            candle_buffer.update(event.symbol, tf, candle)          # legacy
            await get_candle_store().put_candle(event.symbol, tf, candle)  # core.storage
        except Exception:
            logger.exception("candle update failed for %s", event.symbol)
