"""Candle pattern signals: Engulfing, Hammer, Doji, PinBar, InsideBar, Pullback."""

from __future__ import annotations

import statistics

from signals.base import BaseSignal, SignalContext, register


def _cf(c: dict, k: str, default: float = 0.0) -> float:
    """Candle field → float (Bybit иногда шлёт строки)."""
    v = c.get(k)
    return float(v) if v is not None else default


# ──────────────────────────────────────────────
#  CandlePattern — универсальный: пин-бар, молот, дожи, бычье/медвежье поглощение
# ──────────────────────────────────────────────

@register(
    name="candle_pattern",
    description="Engulfing, Hammer, Doji, Pin Bar из 1m свечей",
    category="candle",
    default_score=55,
    cooldown=3600,
)
class CandlePatternSignal(BaseSignal):
    async def check(self, ctx: SignalContext) -> dict | None:
        candles = ctx.candles
        if len(candles) < 3:
            return None

        last, prev = candles[-1], candles[-2]
        o = _cf(last, "open"); h = _cf(last, "high")
        l = _cf(last, "low"); c = _cf(last, "close")
        po = _cf(prev, "open"); pc = _cf(prev, "close")

        body = abs(c - o)
        upper = h - max(c, o)
        lower = min(c, o) - l
        total_range = h - l

        if total_range == 0:
            return None

        results = []

        # 1. Doji — тело < 5% диапазона
        if body / total_range < 0.05 and total_range > 0:
            results.append(("doji", "neutral", 30, {}))

        # 2. Hammer / Shooting Star — нижняя тень > 2x тела, верхняя мала
        if lower > body * 2 and upper < body * 0.3:
            if c > o:
                results.append(("hammer", "buy", 65, {"lower_wick_ratio": round(lower / total_range, 2)}))
            else:
                results.append(("shooting_star", "sell", 60, {"lower_wick_ratio": round(lower / total_range, 2)}))

        # 3. Pin Bar — длинная тень с одной стороны
        wick_ratio = upper / total_range if upper > lower else lower / total_range
        if wick_ratio > 0.6 and body / total_range < 0.3:
            direction = "buy" if lower > upper else "sell"
            results.append(("pin_bar", direction, 60, {"wick_ratio": round(wick_ratio, 2)}))

        # 4. Engulfing — тело поглощает предыдущее
        prev_body = abs(pc - po)
        if prev_body > 0 and body > prev_body * 1.1:
            if c > o and pc < po:
                results.append(("bullish_engulfing", "buy", 70, {}))
            elif c < o and pc > po:
                results.append(("bearish_engulfing", "sell", 65, {}))

        if not results:
            return None

        best = max(results, key=lambda r: r[2])
        return self._result(
            symbol=ctx.symbol,
            score=best[2],
            direction=best[1],
            meta={"pattern": best[0], **best[3]},
        )


# ──────────────────────────────────────────────
#  InsideBar — сужение диапазона
# ──────────────────────────────────────────────

@register(
    name="inside_bar",
    description="Bar внутри предыдущего = сужение диапазона, сквиз",
    category="candle",
    default_score=50,
    cooldown=3600,
)
class InsideBarSignal(BaseSignal):
    async def check(self, ctx: SignalContext) -> dict | None:
        candles = ctx.candles
        if len(candles) < 3:
            return None

        last, prev = candles[-1], candles[-2]
        lh = _cf(last, "high"); ll = _cf(last, "low")
        ph = _cf(prev, "high"); pl = _cf(prev, "low")

        if lh <= ph and ll >= pl:
            streak = 1
            for i in range(2, min(len(candles), 8)):
                ci = candles[-i]; cn = candles[-i - 1]
                if _cf(ci, "high") <= _cf(cn, "high") and _cf(ci, "low") >= _cf(cn, "low"):
                    streak += 1
                else:
                    break

            prev_range = ph - pl
            tightness = 1 - (lh - ll) / prev_range if prev_range > 0 else 0
            score = min(70, 40 + streak * 5 + tightness * 15)
            return self._result(
                symbol=ctx.symbol, score=score, direction="neutral",
                meta={"streak": streak, "tightness": round(tightness, 2)},
            )
        return None


# ──────────────────────────────────────────────
#  Pullback — откат в тренде на снижающемся объёме
# ──────────────────────────────────────────────

@register(
    name="pullback",
    description="Откат против тренда на снижающемся объёме = тренд продолжится",
    category="candle",
    default_score=60,
    cooldown=3600,
)
class PullbackSignal(BaseSignal):
    async def check(self, ctx: SignalContext) -> dict | None:
        candles = ctx.candles
        if len(candles) < 10:
            return None

        first5 = candles[-10:-5]
        trend_up = sum(1 for c in first5 if _cf(c, "close") > _cf(c, "open"))
        trend_down = sum(1 for c in first5 if _cf(c, "close") < _cf(c, "open"))

        if trend_up < 4 and trend_down < 4:
            return None

        direction = "buy" if trend_up >= 4 else "sell"

        last3 = candles[-3:]
        if direction == "buy":
            pullback = all(_cf(c, "close") < _cf(c, "open") for c in last3)
        else:
            pullback = all(_cf(c, "close") > _cf(c, "open") for c in last3)

        if not pullback:
            return None

        vol_trend = [_cf(c, "volume") for c in last3]
        vol_declining = vol_trend[-1] < vol_trend[0] if len(vol_trend) == 3 else False
        score = 60 if vol_declining else 50

        return self._result(
            symbol=ctx.symbol, score=score, direction=direction,
            meta={"pullback_count": 3, "vol_declining": vol_declining},
        )
