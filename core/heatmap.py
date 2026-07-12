"""
Heatmap Engine — рейтинг активов по объёму, моментуму, разворотам и шортам.

Собирает топ:
  - VOLUME  — по 24h turnover (USDT)
  - MOMENTUM — по 24h % change (+ / -)
  - LIQ_HEAT — по сумме ликвидаций за 5 мин
  - REVERSAL — свечи с высоким объёмом и малой ценой (поглощение)

Обновление: раз в 30 секунд
Источник: ticker_store.all + liquidation_store.recent + candle_buffer
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core import SignalResult

logger = logging.getLogger(__name__)


@dataclass
class HeatmapEntry:
    symbol: str = ""
    volume_usdt: float = 0.0
    change_pct: float = 0.0
    liq_notional: float = 0.0
    liq_side: str = ""          # buy / sell
    liq_count: int = 0
    score: float = 0.0          # комбинированная оценка для рейтинга


@dataclass
class HeatmapSnapshot:
    timestamp: float = 0.0
    top_volume: list[dict] = field(default_factory=list)       # [{symbol, volume, change}]
    top_momentum: list[dict] = field(default_factory=list)      # [{symbol, change}]
    bottom_momentum: list[dict] = field(default_factory=list)    # [{symbol, change}]
    top_liq_sell: list[dict] = field(default_factory=list)      # [{symbol, notional, count}]
    top_liq_buy: list[dict] = field(default_factory=list)       # [{symbol, notional, count}]
    liq_total_notional: float = 0.0
    total_symbols: int = 0


class HeatmapEngine:
    """
    Движок тепловой карты рынка.
    Собирает топы по объёму, моментуму, ликвидациям.

    Использование:
        engine = HeatmapEngine(ticker_getter, liq_getter)
        snap = engine.tick()
    """

    def __init__(
        self,
        ticker_getter: Callable,
        liq_getter: Callable,
    ):
        """
        ticker_getter: callable() → dict[symbol, ticker_dict]
            ticker_dict: {symbol, volume_24h, turnover_24h, change_24h, ...}
        liq_getter: callable(minutes=5) → list[liq_dict]
            liq_dict: {symbol, side, notional, ...}
        """
        self._ticker_getter = ticker_getter
        self._liq_getter = liq_getter
        self._last_snapshot: HeatmapSnapshot = HeatmapSnapshot()
        self._top_n = 5

    @property
    def last_snapshot(self) -> HeatmapSnapshot:
        return self._last_snapshot

    def tick(self) -> HeatmapSnapshot:
        """Обновить тепловую карту."""
        all_tickers = self._ticker_getter()
        if not all_tickers:
            return self._last_snapshot

        # --- Volume & Momentum ---
        symbols_data = []
        for sym, t in all_tickers.items():
            short_sym = sym.split("/")[0]
            vol = float(t.get("turnover_24h", t.get("volume_24h", 0)))
            raw_change = float(t.get("change_24h", 0))
            change = raw_change / 100.0 if abs(raw_change) > 1.0 else raw_change
            change_pct = change * 100  # в %
            symbols_data.append({
                "symbol": short_sym,
                "symbol_full": sym,
                "volume_usdt": vol,
                "change_pct": change_pct,
            })

        # Volume top
        by_volume = sorted(symbols_data, key=lambda x: x["volume_usdt"], reverse=True)[:self._top_n]

        # Momentum
        with_momentum = [s for s in symbols_data if abs(s["change_pct"]) > 0.01]
        by_momentum = sorted(with_momentum, key=lambda x: x["change_pct"], reverse=True)
        top_momentum = by_momentum[:self._top_n]
        bottom_momentum = by_momentum[-self._top_n:] if len(by_momentum) >= self._top_n else by_momentum[:]
        bottom_momentum.reverse()

        # --- Liquidations ---
        recent_liqs = self._liq_getter(minutes=5) if self._liq_getter else []
        liq_total = 0.0
        liq_by_sym_sell: dict[str, dict] = {}
        liq_by_sym_buy: dict[str, dict] = {}

        for liq in recent_liqs:
            sym = liq.get("symbol", "?").split("/")[0]
            side = liq.get("side", "sell")
            notional = float(liq.get("notional", 0))
            liq_total += notional

            bucket = liq_by_sym_sell if side == "sell" else liq_by_sym_buy
            if sym not in bucket:
                bucket[sym] = {"symbol": sym, "notional": 0.0, "count": 0}
            bucket[sym]["notional"] += notional
            bucket[sym]["count"] += 1

        # Sort liq sides
        liq_sell = sorted(liq_by_sym_sell.values(), key=lambda x: x["notional"], reverse=True)[:self._top_n]
        liq_buy = sorted(liq_by_sym_buy.values(), key=lambda x: x["notional"], reverse=True)[:self._top_n]

        self._last_snapshot = HeatmapSnapshot(
            timestamp=time.time(),
            top_volume=[
                {"symbol": v["symbol"], "volume_usdt": round(v["volume_usdt"], 0), "change_pct": round(v["change_pct"], 2)}
                for v in by_volume
            ],
            top_momentum=[
                {"symbol": v["symbol"], "change_pct": round(v["change_pct"], 2)}
                for v in top_momentum
            ],
            bottom_momentum=[
                {"symbol": v["symbol"], "change_pct": round(v["change_pct"], 2)}
                for v in bottom_momentum
            ],
            top_liq_sell=[
                {"symbol": v["symbol"], "notional_usdt": round(v["notional"], 0), "count": v["count"]}
                for v in liq_sell
            ],
            top_liq_buy=[
                {"symbol": v["symbol"], "notional_usdt": round(v["notional"], 0), "count": v["count"]}
                for v in liq_buy
            ],
            liq_total_notional=round(liq_total, 0),
            total_symbols=len(symbols_data),
        )
        return self._last_snapshot

    def to_signal(self) -> SignalResult | None:
        """
        Создать Heatmap-сигнал при значимых событиях:
        - Аномальный объём на топ-1
        - Экстремальное расхождение momentum top vs bottom
        - Всплеск ликвидаций (total > 10M USDT)
        """
        snap = self._last_snapshot
        if snap.total_symbols == 0:
            return None

        triggers = []
        score = 0

        # 1. Аномальный объём на лидере (сравниваем с остальными топ-5)
        if snap.top_volume:
            v1 = snap.top_volume[0]["volume_usdt"]
            v2 = snap.top_volume[1]["volume_usdt"] if len(snap.top_volume) > 1 else 1
            if v1 > 0 and v2 > 0 and v1 / v2 > 3.0:
                triggers.append("volume_leader_dominant")
                score += 25

        # 2. Экстремальное расхождение momentum
        if len(snap.top_momentum) >= 3 and len(snap.bottom_momentum) >= 3:
            top_avg = sum(m["change_pct"] for m in snap.top_momentum[:3]) / 3
            bot_avg = sum(m["change_pct"] for m in snap.bottom_momentum[:3]) / 3
            spread = top_avg - bot_avg
            if spread > 15:
                triggers.append("wide_momentum_spread")
                score += 20
            elif spread > 8:
                triggers.append("moderate_momentum_spread")
                score += 10

        # 3. Всплеск ликвидаций
        if snap.liq_total_notional > 10_000_000:  # 10M USDT
            triggers.append("liq_spike")
            score += 20
        elif snap.liq_total_notional > 3_000_000:  # 3M USDT
            triggers.append("liq_moderate")
            score += 10

        # 4. Преобладание sell ликвидаций (каскад)
        sell_total = sum(s["notional_usdt"] for s in snap.top_liq_sell)
        buy_total = sum(s["notional_usdt"] for s in snap.top_liq_buy)
        if sell_total > 0 and buy_total > 0 and sell_total / buy_total > 3:
            triggers.append("sell_liq_dominant")
            score += 15
        if buy_total > 0 and sell_total > 0 and buy_total / sell_total > 3:
            triggers.append("buy_liq_dominant")
            score += 15

        if score < 20:
            return None

        direction = "sell" if "sell_liq_dominant" in triggers or "liq_spike" in triggers else "neutral"
        if "volume_leader_dominant" in triggers and len(triggers) >= 2:
            direction = "buy"

        return SignalResult(
            signal_name="heatmap",
            symbol="MARKET",
            exchange="bybit",
            score=min(score, 95),
            direction=direction,
            meta={
                "top_volume": snap.top_volume,
                "top_momentum": snap.top_momentum,
                "bottom_momentum": snap.bottom_momentum,
                "top_liq_sell": snap.top_liq_sell,
                "top_liq_buy": snap.top_liq_buy,
                "liq_total_notional": snap.liq_total_notional,
                "total_symbols": snap.total_symbols,
                "triggers": triggers,
                "description": f"Heatmap: {', '.join(triggers)}",
            },
            ts=snap.timestamp,
            cooldown=1800,
        )

    def get_info(self) -> dict[str, Any]:
        snap = self._last_snapshot
        return {
            "total": snap.total_symbols,
            "top_volume": snap.top_volume,
            "top_momentum": snap.top_momentum,
            "bottom_momentum": snap.bottom_momentum,
            "liq_total": snap.liq_total_notional,
            "liq_sell": snap.top_liq_sell,
            "liq_buy": snap.top_liq_buy,
        }


_heatmap_engine: HeatmapEngine | None = None


def get_heatmap_engine(ticker_getter=None, liq_getter=None) -> HeatmapEngine:
    global _heatmap_engine
    if _heatmap_engine is None and ticker_getter is not None:
        _heatmap_engine = HeatmapEngine(ticker_getter, liq_getter)
    return _heatmap_engine
