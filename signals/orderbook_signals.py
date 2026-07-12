"""Orderbook signals: spread widening, depth ratio, wall stacked, iceberg."""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core.adaptive import get_adaptive_thresholds


# ──────────────────────────────────────────────
#  SpreadWidening — адаптивный спред
# ──────────────────────────────────────────────

@register(
    name="spread_widening",
    description="Спред шире нормы = низкая ликвидность или высокая волатильность (адаптивный порог)",
    category="orderbook",
    default_score=40,
    cooldown=1800,
)
class SpreadWideningSignal(BaseSignal):
    """
    Определяет аномальный спред.
    В спокойное время (ASIA, LOW vol) спред 0.1% — норма.
    В активное время (NY overlap, HIGH vol) 0.1% — уже сигнал.
    """

    async def check(self, ctx: SignalContext) -> dict | None:
        ob = ctx.orderbook
        if ob is None or ob.best_bid == 0:
            return None

        spd_pct = ob.spread / ob.best_bid

        adaptive = get_adaptive_thresholds()
        threshold = adaptive.get("spread_pct", symbol=ctx.symbol, base=0.001)

        if spd_pct < threshold.value:
            return None

        score = min(70, 40 + spd_pct / threshold.value * 10)
        return self._result(
            symbol=ctx.symbol, score=score, direction="neutral",
            meta={
                "spread": round(ob.spread, 4),
                "spread_pct": round(spd_pct * 100, 3),
                "threshold_pct": round(threshold.value * 100, 3),
                "regime": threshold.regime,
                "session": threshold.session,
            },
        )


# ──────────────────────────────────────────────
#  DepthRatio — концентрация объёма
# ──────────────────────────────────────────────

@register(
    name="depth_ratio",
    description="Концентрация объёма в первых 5 уровнях (внезапный приток)",
    category="orderbook",
    default_score=45,
    cooldown=1800,
)
class DepthRatioSignal(BaseSignal):
    """
    Аномальная концентрация объёма в верхних уровнях стакана.
    В спокойное время 70% на 5 уровней — норма.
    В волатильное время — уже 60% сигнал.
    """

    async def check(self, ctx: SignalContext) -> dict | None:
        ob = ctx.orderbook
        if ob is None or len(ob.bids) < 10 or len(ob.asks) < 10:
            return None

        bid_top5 = sum(b[1] for b in ob.bids[:5])
        bid_all = sum(b[1] for b in ob.bids[:20])
        ask_top5 = sum(a[1] for a in ob.asks[:5])
        ask_all = sum(a[1] for a in ob.asks[:20])

        bid_ratio = bid_top5 / bid_all if bid_all > 0 else 0
        ask_ratio = ask_top5 / ask_all if ask_all > 0 else 0

        adaptive = get_adaptive_thresholds()
        high_th = adaptive.get("depth_ratio_high", symbol=ctx.symbol, base=0.7)
        low_th = adaptive.get("depth_ratio_low", symbol=ctx.symbol, base=0.2)

        factors = []
        if bid_ratio > high_th.value:
            factors.append((f"Bid concentration ({bid_ratio:.0%})", "buy", 15))
        if ask_ratio > high_th.value:
            factors.append((f"Ask concentration ({ask_ratio:.0%})", "sell", 15))
        if bid_ratio < low_th.value:
            factors.append((f"Bid thin ({bid_ratio:.0%})", "sell", 10))
        if ask_ratio < low_th.value:
            factors.append((f"Ask thin ({ask_ratio:.0%})", "buy", 10))

        if not factors:
            return None

        best = max(factors, key=lambda f: f[2])
        score = min(60, 40 + best[2])
        return self._result(
            symbol=ctx.symbol, score=score, direction=best[1],
            meta={"bid_ratio": round(bid_ratio, 2), "ask_ratio": round(ask_ratio, 2),
                  "regime": high_th.regime, "session": high_th.session},
        )


# ──────────────────────────────────────────────
#  WallStacked — множественные стенки
# ──────────────────────────────────────────────

@register(
    name="wall_stacked",
    description="Множественные крупные стены на одной стороне стакана",
    category="orderbook",
    default_score=55,
    cooldown=3600,
)
class WallStackedSignal(BaseSignal):
    """Стены детектятся через ob.detect_walls() — порог там тоже адаптивный."""

    async def check(self, ctx: SignalContext) -> dict | None:
        ob = ctx.orderbook
        if ob is None:
            return None

        adaptive = get_adaptive_thresholds()
        wall_th = adaptive.get("wall_threshold_ratio", symbol=ctx.symbol, base=5.0)

        walls = ob.detect_walls(threshold_ratio=wall_th.value)
        bid_walls = walls.get("bid_walls", [])
        ask_walls = walls.get("ask_walls", [])

        min_w = adaptive.get("min_walls", symbol=ctx.symbol, base=2)

        if len(bid_walls) >= min_w.value:
            score = min(70, 50 + len(bid_walls) * 10)
            return self._result(
                symbol=ctx.symbol, score=score, direction="buy",
                meta={"bid_walls": len(bid_walls), "ask_walls": len(ask_walls),
                      "wall_threshold": round(wall_th.value, 1), "regime": wall_th.regime},
            )
        if len(ask_walls) >= min_w.value:
            score = min(70, 50 + len(ask_walls) * 10)
            return self._result(
                symbol=ctx.symbol, score=score, direction="sell",
                meta={"bid_walls": len(bid_walls), "ask_walls": len(ask_walls),
                      "wall_threshold": round(wall_th.value, 1), "regime": wall_th.regime},
            )
        return None


# ──────────────────────────────────────────────
#  Iceberg — айсберг-ордера
# ──────────────────────────────────────────────

@register(
    name="iceberg",
    description="Обнаружение айсберг-ордеров (одинаковые объёмы на соседних уровнях)",
    category="orderbook",
    default_score=55,
    cooldown=3600,
)
class IcebergSignal(BaseSignal):
    async def check(self, ctx: SignalContext) -> dict | None:
        ob = ctx.orderbook
        if ob is None:
            return None

        if ob.detect_iceberg(levels_similar=3):
            if ob.bid_volume > ob.ask_volume:
                direction = "buy"
                score = 60
            else:
                direction = "sell"
                score = 55
            return self._result(
                symbol=ctx.symbol, score=score, direction=direction,
                meta={"bid_volume": round(ob.bid_volume, 2), "ask_volume": round(ob.ask_volume, 2)},
            )
        return None
