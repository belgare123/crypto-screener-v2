"""Candle technical signals: RSI, Momentum, Consecutive candles, Volume+Body (адаптивные пороги)."""

from __future__ import annotations

import statistics
from typing import Any

from signals.base import BaseSignal, SignalContext, register
from core.adaptive import get_adaptive_thresholds


def _cf(c: dict, k: str, default: float = 0.0) -> float:
    """Candle field → float (Bybit иногда шлёт строки)."""
    v = c.get(k)
    return float(v) if v is not None else default


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """Calculate RSI from close prices list."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[-i] - closes[-i - 1]
        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    avg_gain = statistics.mean(gains[:period]) if gains else 0
    avg_loss = statistics.mean(losses[:period]) if losses else 0
    if avg_loss < 1e-9:
        return 100.0 if avg_gain > 0 else 50.0
    if avg_gain < 1e-9:
        return 0.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ──────────────────────────────────────────────
#  RSI — перекупленность / перепроданность
#  Пороги адаптивные: в тренде допускаем более глубокий RSI
# ──────────────────────────────────────────────

@register(
    name="rsi",
    description="RSI(14) — oversold/overbought с адаптивными порогами",
    category="candle",
    default_score=65,
    cooldown=1800,
)
class RSISignal(BaseSignal):
    async def check(self, ctx: SignalContext) -> dict | None:
        candles = ctx.candles
        if len(candles) < 15:
            return None
        closes = [_cf(c, "close") for c in candles if c.get("close") is not None]
        if len(closes) < 15:
            return None
        rsi_val = _rsi(closes, 14)
        if rsi_val is None:
            return None

        adaptive = get_adaptive_thresholds()
        oversold = adaptive.get("rsi_oversold", symbol=ctx.symbol, base=30)
        overbought = adaptive.get("rsi_overbought", symbol=ctx.symbol, base=70)

        # В высокой волатильности RSI чаще экстремален — сужаем пороги
        # В штиле RSI редко выходит за 30/70 — расширяем (но base уже учтён vol_mult)
        actual_oversold = oversold.value
        actual_overbought = overbought.value

        if rsi_val < actual_oversold:
            depth = (actual_oversold - rsi_val) / actual_oversold  # 0-1
            score = min(85, 60 + depth * 40)
            return self._result(
                symbol=ctx.symbol, score=round(score, 1), direction="buy",
                meta={"rsi": round(rsi_val, 1), "condition": "oversold",
                      "threshold": round(actual_oversold, 1), "regime": oversold.regime},
            )
        if rsi_val > actual_overbought:
            depth = (rsi_val - actual_overbought) / (100 - actual_overbought)
            score = min(80, 55 + depth * 40)
            return self._result(
                symbol=ctx.symbol, score=round(score, 1), direction="sell",
                meta={"rsi": round(rsi_val, 1), "condition": "overbought",
                      "threshold": round(actual_overbought, 1), "regime": overbought.regime},
            )
        return None


# ──────────────────────────────────────────────
#  Momentum — резкое движение за 1 свечу
# ──────────────────────────────────────────────

@register(
    name="momentum",
    description="Close - Open > адаптивный % = сильный импульс",
    category="candle",
    default_score=50,
    cooldown=900,
)
class MomentumSignal(BaseSignal):
    async def check(self, ctx: SignalContext) -> dict | None:
        candles = ctx.candles
        if len(candles) < 3:
            return None
        last = candles[-1]
        lo = _cf(last, "open")
        lc = _cf(last, "close")
        if lo == 0:
            return None
        body_pct = abs(lc - lo) / lo * 100

        adaptive = get_adaptive_thresholds()
        threshold = adaptive.get("momentum_pct", symbol=ctx.symbol, base=0.5)

        if body_pct < threshold.value:
            return None

        direction = "buy" if lc > lo else "sell"
        score = min(70, 50 + body_pct * 5)
        return self._result(
            symbol=ctx.symbol, score=score, direction=direction,
            meta={"body_pct": round(body_pct, 2), "close": lc,
                  "threshold": round(threshold.value, 2), "regime": threshold.regime},
        )


# ──────────────────────────────────────────────
#  Consecutive — N+ свечей одного цвета
# ──────────────────────────────────────────────

@register(
    name="consecutive",
    description="5+ одноцветных свечей подряд = тренд (адаптивный порог)",
    category="candle",
    default_score=55,
    cooldown=3600,
)
class ConsecutiveSignal(BaseSignal):
    async def check(self, ctx: SignalContext) -> dict | None:
        candles = ctx.candles
        adaptive = get_adaptive_thresholds()
        threshold = adaptive.get("consecutive_count", symbol=ctx.symbol, base=5)
        min_run = int(round(threshold.value))

        if len(candles) < min_run:
            return None

        run = 1
        direction = None
        for i in range(1, min(len(candles), 30)):
            cur = candles[-i]
            prev = candles[-i - 1]
            cc = _cf(cur, "close")
            co = _cf(cur, "open")
            pc = _cf(prev, "close")
            po = _cf(prev, "open")
            if cc > co and pc > po:
                run += 1
                direction = "buy"
            elif cc < co and pc < po:
                run += 1
                direction = "sell"
            else:
                break

        if run < min_run:
            return None

        score = min(80, 50 + run * 3)
        return self._result(
            symbol=ctx.symbol, score=score, direction=direction or "neutral",
            meta={"run_length": run, "min_run": min_run, "regime": threshold.regime},
        )


# ──────────────────────────────────────────────
#  VolumeBody — большое тело + аномальный объём
# ──────────────────────────────────────────────

@register(
    name="volume_body",
    description="Большое тело свечи + объём > avg × адаптивный множитель = импульс",
    category="candle",
    default_score=55,
    cooldown=1800,
)
class VolumeBodySignal(BaseSignal):
    BODY_MIN_PCT = 0.3  # тело минимум 0.3% — не меняем, это константа качества

    async def check(self, ctx: SignalContext) -> dict | None:
        candles = ctx.candles
        if len(candles) < 10:
            return None
        last = candles[-1]
        lo = _cf(last, "open")
        lc = _cf(last, "close")
        lv = _cf(last, "volume")
        if lo == 0:
            return None
        body_pct = abs(lc - lo) / lo * 100
        if body_pct < self.BODY_MIN_PCT:
            return None

        vols = [_cf(c, "volume") for c in candles[-10:] if c.get("volume") is not None]
        if not vols:
            return None
        avg_vol = statistics.mean(vols)
        if avg_vol == 0:
            return None

        adaptive = get_adaptive_thresholds()
        threshold = adaptive.get("volume_multiplier", symbol=ctx.symbol, base=1.5)

        if lv < avg_vol * threshold.value:
            return None

        vol_ratio = lv / avg_vol
        direction = "buy" if lc > lo else "sell"
        score = min(80, 50 + body_pct * 3 + vol_ratio * 5)
        return self._result(
            symbol=ctx.symbol, score=score, direction=direction,
            meta={"body_pct": round(body_pct, 2), "vol_ratio": round(vol_ratio, 2),
                  "threshold": round(threshold.value, 1), "regime": threshold.regime},
        )
