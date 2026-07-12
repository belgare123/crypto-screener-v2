"""
OHLCV Feature Calculator — свечи, VWAP, ATR, Volume Profile.
Подписывается на свечные каналы (1m, 5m, 15m) и вычисляет
производные признаки на их основе.

Производит фичи:
- ohlcv.{tf}.last: dict — последняя свеча
- ohlcv.{tf}.buffer: list[dict] — буфер свечей (200 шт)
- vwap.{tf}: float — VWAP за период
- atr.{tf}: float — ATR(14)
- vol_profile.{tf}: dict — профиль объёма
"""

from __future__ import annotations

import logging
import statistics
import time
from collections import defaultdict, deque

from core import Event
from core.features.base import BaseFeatureCalculator

logger = logging.getLogger(__name__)


class OHLCVFeatureCalculator(BaseFeatureCalculator):
    """
    Калькулятор OHLCV-признаков.
    Держит буфер свечей и вычисляет VWAP, ATR, Volume Profile.
    """

    event_channels = ["candles.*"]  # candles.BTCUSDT.1m, candles.BTCUSDT.5m, etc.
    feature_names = [
        "ohlcv.1m.last",
        "ohlcv.1m.buffer",
        "ohlcv.5m.buffer",
        "ohlcv.15m.buffer",
        "vwap.1m",
        "vwap.5m",
        "atr.14",
        "vol_profile.1m",
    ]
    default_ttl = 60.0  # свечи обновляются раз в минуту
    priority = 20

    BUFFER_SIZE = 200

    def __init__(self, feature_store=None):
        super().__init__(feature_store)
        # Буферы: (symbol, timeframe) -> deque
        self._buffers: dict[tuple[str, str], deque] = defaultdict(
            lambda: deque(maxlen=self.BUFFER_SIZE)
        )
        # Последняя свеча для каждого (symbol, timeframe)
        self._last_candle: dict[tuple[str, str], dict] = {}

    async def compute(self, symbol: str, event: Event | None = None) -> dict:
        """Вычислить OHLCV-фичи для symbol."""
        features = {}

        # 1m
        buf_1m = self._get_buffer(symbol, "1m")
        buf_1m_list = list(buf_1m)
        features["ohlcv.1m.last"] = buf_1m_list[-1] if buf_1m_list else None
        features["ohlcv.1m.buffer"] = buf_1m_list

        # 5m
        buf_5m = self._get_buffer(symbol, "5m")
        buf_5m_list = list(buf_5m)
        features["ohlcv.5m.buffer"] = buf_5m_list if buf_5m_list else None

        # 15m
        buf_15m = self._get_buffer(symbol, "15m")
        buf_15m_list = list(buf_15m)
        features["ohlcv.15m.buffer"] = buf_15m_list if buf_15m_list else None

        # VWAP (1m, 5m)
        features["vwap.1m"] = self._calc_vwap(buf_1m, 20)
        features["vwap.5m"] = self._calc_vwap(buf_5m, 12) if buf_5m_list else None

        # ATR(14) на 1m
        atr_value = self._calc_atr(buf_1m, 14)
        features["atr.14"] = atr_value

        # Volume Profile
        features["vol_profile.1m"] = self._calc_vol_profile(buf_1m, 24)

        return features

    async def on_event(self, event: Event):
        """Обновить буфер свечей при получении новой свечи."""
        parts = event.channel.split(".")
        # Формат канала: candles.1m.BTC/USDT:USDT (с tf) или candles.BTC/USDT:USDT (без tf)
        if len(parts) >= 3 and parts[1].endswith("m"):
            tf = parts[1]  # "1m", "5m", "15m"
        else:
            tf = "1m"  # fallback для старого формата candles.SYMBOL

        candle = event.data
        if isinstance(candle, list):
            candle = candle[0] if candle else {}

        normalized = self._normalize_candle(candle)
        if normalized is None:
            return

        key = (event.symbol, tf)
        buf = self._buffers[key]
        # Проверка дубликатов
        if buf and buf[-1].get("timestamp") == normalized.get("timestamp"):
            buf[-1] = normalized
        else:
            buf.append(normalized)
        self._last_candle[key] = normalized

        await super().on_event(event)

    def _get_buffer(self, symbol: str, tf: str) -> deque:
        return self._buffers.get((symbol, tf), deque())

    def _normalize_candle(self, candle: dict) -> dict | None:
        """Нормализовать свечу из любого формата биржи."""
        try:
            # Bybit-формат: o, h, l, c, v, qv, t
            if "v" in candle or "t" in candle:
                ts = candle.get("t", candle.get("timestamp", time.time() * 1000))
                if isinstance(ts, (int, float)) and ts > 1e10:
                    ts = ts / 1000
                return {
                    "timestamp": ts,
                    "open": float(candle.get("o", 0)),
                    "high": float(candle.get("h", 0)),
                    "low": float(candle.get("l", 0)),
                    "close": float(candle.get("c", 0)),
                    "volume": float(candle.get("v", 0)),
                    "quote_volume": float(candle.get("qv", candle.get("quote_volume", 0))),
                }
            # Стандартный формат
            return {
                "timestamp": candle.get("timestamp", time.time()),
                "open": float(candle.get("open", 0)),
                "high": float(candle.get("high", 0)),
                "low": float(candle.get("low", 0)),
                "close": float(candle.get("close", 0)),
                "volume": float(candle.get("volume", 0)),
                "quote_volume": float(candle.get("quote_volume", 0)),
            }
        except (ValueError, TypeError):
            return None

    def _calc_vwap(self, buf: deque, period: int) -> float | None:
        """VWAP = sum(price * volume) / sum(volume) за period свечей."""
        candles = list(buf)[-period:]
        if len(candles) < period:
            return None
        total_pv = 0.0
        total_v = 0.0
        for c in candles:
            typical = (c["high"] + c["low"] + c["close"]) / 3
            vol = c["volume"]
            total_pv += typical * vol
            total_v += vol
        return round(total_pv / total_v, 4) if total_v > 0 else None

    def _calc_atr(self, buf: deque, period: int = 14) -> float:
        """ATR(period) — средний истинный диапазон."""
        candles = list(buf)
        if len(candles) < period + 1:
            return 0.0
        trs = []
        for i in range(-period, 0):
            try:
                prev_close = candles[i - 1]["close"]
                high = candles[i]["high"]
                low = candles[i]["low"]
                tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
                trs.append(tr)
            except (IndexError, KeyError):
                continue
        if not trs:
            return 0.0
        return round(sum(trs) / len(trs), 4)

    def _calc_vol_profile(self, buf: deque, bins: int = 24) -> dict:
        """
        Профиль объёма: распределение объёма по ценовым уровням.
        Возвращает {level: volume, ...} — упрощённая версия.
        """
        candles = list(buf)[-bins:]
        if not candles:
            return {}
        prices = []
        volumes = []
        for c in candles:
            prices.append(c["high"])
            prices.append(c["low"])
            volumes.append(c["volume"])
        if not prices:
            return {}
        min_p = min(prices)
        max_p = max(prices)
        if max_p == min_p:
            return {}
        step = (max_p - min_p) / 10
        profile: dict[str, float] = {}
        for c in candles:
            level = round((c["low"] + c["high"]) / 2 / step) * step
            level_key = f"{level:.2f}"
            profile[level_key] = profile.get(level_key, 0) + c["volume"]
        return {
            "levels": dict(sorted(profile.items())),
            "poc": max(profile, key=profile.get) if profile else None,
            "poc_volume": max(profile.values()) if profile else 0,
        }
