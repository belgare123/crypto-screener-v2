"""
Liquidity Zones — авто-построение уровней поддержки/сопротивления.

Использует:
  - Свечные данные (1m, 5m) для определения swing highs/lows
  - Orderbook walls (BTC/ETH) для уровней с высокой ликвидностью

Обновление: раз в 60 секунд для всех отслеживаемых символов.

Swing detection:
  - Local top: high > lookback_left и high > lookback_right соседей
  - Local bottom: low < lookback_left и low < lookback_right соседей
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from core import SignalResult

logger = logging.getLogger(__name__)

LOOKBACK = 3       # кол-во свечей с каждой стороны для swing detection
MIN_SWING_BIAS = 0.001  # мин. отклонение для разворота (0.1%)

# Символы, для которых доступен ордербук
ORDERBOOK_SYMBOLS = {"BTC", "ETH"}


@dataclass
class LiquidityZone:
    price: float = 0.0
    zone_type: str = ""      # resistance / support / wall_bid / wall_ask
    strength: float = 0.0    # 0-100 (сила уровня)
    touches: int = 0         # сколько раз цена касалась
    range_high: float = 0.0  # верхняя граница зоны
    range_low: float = 0.0   # нижняя граница зоны


@dataclass
class LiquiditySnapshot:
    symbol: str = ""
    timestamp: float = 0.0
    current_price: float = 0.0
    zones: list[LiquidityZone] = field(default_factory=list)
    nearest_support: float = 0.0
    nearest_resistance: float = 0.0
    total_bid_wall: float = 0.0     # объём бид-стены (если есть)
    total_ask_wall: float = 0.0     # объём аск-стены (если есть)
    has_orderbook: bool = False


class LiquidityZoneEngine:
    """
    Движок ликвидности — находит уровни поддержки/сопротивления.
    """

    def __init__(self, candle_getter: Callable, ob_getter: Callable):
        """
        candle_getter(symbol, timeframe, count) → list[dict]
            каждой свечи: {high, low, open, close, volume, ...}
        ob_getter(symbol) → dict | None
            {bids: [[price, size], ...], asks: [[price, size], ...]}
        """
        self._candle_getter = candle_getter
        self._ob_getter = ob_getter
        self._snapshots: dict[str, LiquiditySnapshot] = {}
        self._lookback = LOOKBACK

    @property
    def last_snapshots(self) -> dict[str, LiquiditySnapshot]:
        return dict(self._snapshots)

    def get_snapshot(self, symbol: str) -> LiquiditySnapshot | None:
        return self._snapshots.get(symbol)

    def analyze(self, symbol: str) -> LiquiditySnapshot:
        """Проанализировать liquidity зоны для символа."""
        short_sym = symbol.split("/")[0]
        candles_1m = self._candle_getter(symbol, "1", 60)
        candles_5m = self._candle_getter(symbol, "5", 30)
        ob = self._ob_getter(symbol) if short_sym in ORDERBOOK_SYMBOLS else None

        current_price = 0.0
        zones = []

        # ---- Swing High/Low detection на 1m ----
        if candles_1m and len(candles_1m) > self._lookback * 2:
            current_price = float(candles_1m[-1].get("close", 0))
            swing_lows = self._find_swing_lows(candles_1m)
            swing_highs = self._find_swing_highs(candles_1m)

            for sl in swing_lows:
                zones.append(LiquidityZone(
                    price=sl["price"],
                    zone_type="support",
                    strength=sl["strength"],
                    touches=sl["touches"],
                    range_low=sl["range_low"],
                    range_high=sl["range_high"],
                ))

            for sh in swing_highs:
                zones.append(LiquidityZone(
                    price=sh["price"],
                    zone_type="resistance",
                    strength=sh["strength"],
                    touches=sh["touches"],
                    range_low=sh["range_low"],
                    range_high=sh["range_high"],
                ))

            # Дополнительно из 5m
            if candles_5m and len(candles_5m) > self._lookback * 2:
                sl5 = self._find_swing_lows(candles_5m)
                sh5 = self._find_swing_highs(candles_5m)
                for sl in sl5:
                    # Проверяем дубликат
                    if not any(abs(z.price - sl["price"]) / sl["price"] < 0.002 for z in zones if z.zone_type == "support"):
                        zones.append(LiquidityZone(
                            price=sl["price"],
                            zone_type="support",
                            strength=sl["strength"],
                            touches=sl["touches"],
                            range_low=sl["range_low"],
                            range_high=sl["range_high"],
                        ))
                for sh in sh5:
                    if not any(abs(z.price - sh["price"]) / sh["price"] < 0.002 for z in zones if z.zone_type == "resistance"):
                        zones.append(LiquidityZone(
                            price=sh["price"],
                            zone_type="resistance",
                            strength=sh["strength"],
                            touches=sh["touches"],
                            range_low=sh["range_low"],
                            range_high=sh["range_high"],
                        ))

        # ---- Orderbook walls (только BTC/ETH) ----
        bid_wall_size = 0.0
        ask_wall_size = 0.0
        if ob:
            bids = ob.bids or []
            asks = ob.asks or []

            # Найти макс. кластер объёма на бид и аск
            if bids:
                bid_walls = sorted(bids, key=lambda x: float(x[1]), reverse=True)[:3]
                for b in bid_walls:
                    price = float(b[0])
                    size = float(b[1])
                    bid_wall_size += size * price  # notional
                    # Добавляем как зону, если нет дубликата
                    if not any(abs(z.price - price) / price < 0.001 for z in zones):
                        zones.append(LiquidityZone(
                            price=price,
                            zone_type="wall_bid",
                            strength=min(size * price / 1_000_000 * 10, 90),  # пропорционально
                            touches=0,
                            range_low=price * 0.998,
                            range_high=price,
                        ))

            if asks:
                ask_walls = sorted(asks, key=lambda x: float(x[1]), reverse=True)[:3]
                for a in ask_walls:
                    price = float(a[0])
                    size = float(a[1])
                    ask_wall_size += size * price
                    if not any(abs(z.price - price) / price < 0.001 for z in zones):
                        zones.append(LiquidityZone(
                            price=price,
                            zone_type="wall_ask",
                            strength=min(size * price / 1_000_000 * 10, 90),
                            touches=0,
                            range_low=price,
                            range_high=price * 1.002,
                        ))

        # Сортируем зоны по цене
        zones.sort(key=lambda z: z.price)

        # Ближайшие support/resistance
        nearest_support = 0.0
        nearest_resistance = 0.0
        if current_price > 0 and zones:
            supports = [z for z in zones if z.zone_type in ("support", "wall_bid") and z.price < current_price]
            resistances = [z for z in zones if z.zone_type in ("resistance", "wall_ask") and z.price > current_price]
            if supports:
                nearest_support = max(supports, key=lambda z: z.price).price
            if resistances:
                nearest_resistance = min(resistances, key=lambda z: z.price).price

        snap = LiquiditySnapshot(
            symbol=short_sym,
            timestamp=time.time(),
            current_price=current_price,
            zones=zones,
            nearest_support=nearest_support,
            nearest_resistance=nearest_resistance,
            total_bid_wall=bid_wall_size,
            total_ask_wall=ask_wall_size,
            has_orderbook=ob is not None,
        )

        self._snapshots[symbol] = snap
        return snap

    def _find_swing_lows(self, candles: list[dict]) -> list[dict]:
        """Найти локальные минимумы."""
        lb = self._lookback
        if len(candles) < lb * 2 + 1:
            return []

        lows = []
        for i in range(lb, len(candles) - lb):
            current_low = float(candles[i].get("low", 0))
            # Все слева выше
            left_ok = all(float(candles[i - j].get("low", 0)) > current_low for j in range(1, lb + 1))
            # Все справа выше
            right_ok = all(float(candles[i + j].get("low", 0)) > current_low for j in range(1, lb + 1))

            if left_ok and right_ok:
                # Сила = чем больше свечей слева выше, тем сильнее
                left_count = sum(1 for j in range(1, lb + 1) if (float(candles[i - j].get("low", 0)) - current_low) / max(current_low, 1) > MIN_SWING_BIAS)
                right_count = sum(1 for j in range(1, lb + 1) if (float(candles[i + j].get("low", 0)) - current_low) / max(current_low, 1) > MIN_SWING_BIAS)
                strength = min((left_count + right_count) / (lb * 2) * 100, 100)

                # Область уровня: средний high ближайших свечей до/после
                low_range = min(float(candles[i + j].get("low", current_low)) for j in range(-lb, lb + 1))
                high_range = max(float(candles[i + j].get("high", current_low)) for j in range(-lb, lb + 1))

                # Определяем кол-во касаний цены этой зоны в истории
                touches = sum(1 for c in candles[max(0, i - 20):i + lb + 1]
                             if abs(float(c.get("close", 0)) - current_low) / max(current_low, 1) < 0.003)

                lows.append({
                    "price": current_low,
                    "strength": round(strength, 0),
                    "touches": touches,
                    "range_low": low_range,
                    "range_high": high_range,
                })

        return lows

    def _find_swing_highs(self, candles: list[dict]) -> list[dict]:
        """Найти локальные максимумы."""
        lb = self._lookback
        if len(candles) < lb * 2 + 1:
            return []

        highs = []
        for i in range(lb, len(candles) - lb):
            current_high = float(candles[i].get("high", 0))
            left_ok = all(float(candles[i - j].get("high", 0)) < current_high for j in range(1, lb + 1))
            right_ok = all(float(candles[i + j].get("high", 0)) < current_high for j in range(1, lb + 1))

            if left_ok and right_ok:
                left_count = sum(1 for j in range(1, lb + 1) if (current_high - float(candles[i - j].get("high", 0))) / max(current_high, 1) > MIN_SWING_BIAS)
                right_count = sum(1 for j in range(1, lb + 1) if (current_high - float(candles[i + j].get("high", 0))) / max(current_high, 1) > MIN_SWING_BIAS)
                strength = min((left_count + right_count) / (lb * 2) * 100, 100)

                high_range = max(float(candles[i + j].get("high", current_high)) for j in range(-lb, lb + 1))
                low_range = min(float(candles[i + j].get("low", current_high)) for j in range(-lb, lb + 1))

                touches = sum(1 for c in candles[max(0, i - 20):i + lb + 1]
                             if abs(float(c.get("close", 0)) - current_high) / max(current_high, 1) < 0.003)

                highs.append({
                    "price": current_high,
                    "strength": round(strength, 0),
                    "touches": touches,
                    "range_low": low_range,
                    "range_high": high_range,
                })

        return highs

    def to_signal(self, symbol: str) -> SignalResult | None:
        """Сигнал: цена приближается к сильному уровню (в пределах 1%)."""
        snap = self.get_snapshot(symbol)
        if not snap or snap.current_price == 0:
            return None

        price = snap.current_price
        signal_parts = []

        # Проверяем ближайшие зоны
        for zone in snap.zones:
            if zone.strength < 50:
                continue
            distance = abs(price - zone.price) / max(price, 1) * 100  # %
            if distance < 0.5:
                signal_parts.append({
                    "zone_type": zone.zone_type,
                    "price": zone.price,
                    "distance_pct": round(distance, 2),
                    "strength": zone.strength,
                })

        if not signal_parts:
            return None

        score = max(p["strength"] for p in signal_parts)
        has_resistance = any(p["zone_type"] in ("resistance", "wall_ask") for p in signal_parts)
        has_support = any(p["zone_type"] in ("support", "wall_bid") for p in signal_parts)

        direction = "sell" if has_resistance else ("buy" if has_support else "neutral")

        return SignalResult(
            signal_name="liquidity_zone",
            symbol=symbol,
            exchange="bybit",
            score=min(score, 95),
            direction=direction,
            meta={
                "current_price": price,
                "nearby_zones": signal_parts,
                "nearest_support": snap.nearest_support,
                "nearest_resistance": snap.nearest_resistance,
                "description": f"Приближение к зоне ликвидности ({len(signal_parts)} ур.)",
            },
            ts=snap.timestamp,
            cooldown=1800,
        )

    def get_info(self, symbol: str) -> dict[str, Any]:
        snap = self.get_snapshot(symbol)
        if not snap:
            return {}
        return {
            "symbol": snap.symbol,
            "price": snap.current_price,
            "nearest_support": snap.nearest_support,
            "nearest_resistance": snap.nearest_resistance,
            "zones": [
                {"price": z.price, "type": z.zone_type, "strength": z.strength, "touches": z.touches}
                for z in snap.zones
            ],
        }


_liquidity_engine: LiquidityZoneEngine | None = None


def get_liquidity_engine(candle_getter=None, ob_getter=None) -> LiquidityZoneEngine:
    global _liquidity_engine
    if _liquidity_engine is None and candle_getter is not None:
        _liquidity_engine = LiquidityZoneEngine(candle_getter, ob_getter)
    return _liquidity_engine
