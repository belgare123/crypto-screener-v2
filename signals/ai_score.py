"""AI Score — единый агрегированный балл (Volume + Whale + CVD + OB + Liq + Momentum)."""

from __future__ import annotations

import statistics
import time

from signals.base import BaseSignal, SignalContext, register


def _cf(c: dict, k: str, default: float = 0.0) -> float:
    v = c.get(k)
    return float(v) if v is not None else default


# ──────────────────────────────────────────────
#  Вспомогательные индикаторы
# ──────────────────────────────────────────────

def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return statistics.mean(values[-period:])


def _ema(values: list[float], period: int) -> float | None:
    """Простая EMA (первая = SMA, остальные alpha)."""
    if len(values) < period + 1:
        return None
    alpha = 2 / (period + 1)
    ema = _sma(values[:period], period)
    if ema is None:
        return None
    for v in values[period:]:
        ema = v * alpha + ema * (1 - alpha)
    return ema


def _atr(candles: list[dict], period: int = 14) -> float | None:
    """ATR(14) из 1m свечей."""
    if len(candles) < period + 1:
        return None
    tr_values: list[float] = []
    for i in range(1, len(candles)):
        h = _cf(candles[-i], "high")
        l = _cf(candles[-i], "low")
        pc = _cf(candles[-i - 1], "close")
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_values.append(tr)
    return _ema(tr_values, period)


# ══════════════════════════════════════════════
#  AI Score Signal
# ══════════════════════════════════════════════

@register(
    name="ai_score",
    description="AI Score: единая оценка на базе Volume + Whale + CVD + Orderbook + Liquidation",
    category="ai",
    default_score=0,
    cooldown=1800,  # 30 мин
)
class AIScoreSignal(BaseSignal):
    """Главный агрегированный сигнал: единый балл 0-100 + разбивка по факторам."""

    async def check(self, ctx: SignalContext) -> dict | None:
        factors: list[tuple[str, float, str, str]] = []  # (label, score, direction, icon)
        buy_score = 0.0
        sell_score = 0.0

        candles = ctx.candles
        if len(candles) < 20:
            return None

        # ── Режим рынка ──
        regime = self._detect_regime(candles)
        regime_label = regime["label"]
        regime_icon = regime["icon"]

        # ── 1. Volume Factor (0-25) ──
        if len(candles) >= 10:
            v_factor = self._score_volume(candles)
            factors.append(v_factor)
            if v_factor[2] == "buy":
                buy_score += v_factor[1]
            elif v_factor[2] == "sell":
                sell_score += v_factor[1]

        # ── 2. Whale Factor (0-25) ──
        w_factor = self._score_whale(ctx.whale_trades)
        if w_factor:
            factors.append(w_factor)
            if w_factor[2] == "buy":
                buy_score += w_factor[1]
            elif w_factor[2] == "sell":
                sell_score += w_factor[1]

        # ── 3. CVD Factor (0-20) ──
        c_factor = self._score_cvd(ctx.cvd)
        if c_factor:
            factors.append(c_factor)
            if c_factor[2] == "buy":
                buy_score += c_factor[1]
            elif c_factor[2] == "sell":
                sell_score += c_factor[1]

        # ── 4. Orderbook Factor (0-20) ──
        o_factor = self._score_orderbook(ctx.orderbook)
        if o_factor:
            factors.append(o_factor)
            if o_factor[2] == "buy":
                buy_score += o_factor[1]
            elif o_factor[2] == "sell":
                sell_score += o_factor[1]

        # ── 5. Liquidation Factor (0-20) ──
        l_factor = self._score_liquidation(ctx.liquidations)
        if l_factor:
            factors.append(l_factor)
            if l_factor[2] == "buy":
                buy_score += l_factor[1]
            elif l_factor[2] == "sell":
                sell_score += l_factor[1]

        # ── 6. Momentum Factor (0-10) ──
        m_factor = self._score_momentum(candles)
        if m_factor:
            factors.append(m_factor)
            if m_factor[2] == "buy":
                buy_score += m_factor[1]
            elif m_factor[2] == "sell":
                sell_score += m_factor[1]

        # ── Итог ──
        total = buy_score + sell_score
        if total < 20:
            return None

        direction = "buy" if buy_score > sell_score else "sell"
        if buy_score == sell_score:
            direction = "neutral"

        # Confidence (0-100): насколько факторы согласованы
        direction_count = len([f for f in factors if f[2] == direction])
        total_factors = len(factors) or 1
        agreement = direction_count / total_factors
        confidence = min(100, total * (0.6 + agreement * 0.4))

        # ── Multi-Timeframe Confluence ──
        confluence = self._score_confluence(ctx)
        tf_direction = direction  # итоговое направление
        tf_stars = 0
        if confluence:
            tf_direction = confluence.get("direction", direction)
            tf_stars = confluence.get("stars", 0)

        # Итоговый score 0-100
        final_score = min(100, total)

        timestamp = int(time.time())
        return self._result(
            symbol=ctx.symbol,
            score=final_score,
            direction=direction,
            meta={
                "ai_score": final_score,
                "confidence": round(confidence, 1),
                "direction": direction,
                "regime": regime_label,
                "regime_icon": regime_icon,
                "factors": [(f[0], f[1], f[3]) for f in factors],
                "buy_score": round(buy_score, 1),
                "sell_score": round(sell_score, 1),
                "agreement": round(agreement * 100, 1),
                "confluence": confluence,
                "tf_stars": tf_stars,
                "tf_direction": tf_direction,
                "ts": timestamp,
                "depth": {
                    "atr_pct": regime.get("atr_pct"),
                    "vol_pct": regime.get("vol_pct"),
                    "trend": regime.get("trend"),
                },
            },
        )

    # ─────────── ПОДСИСТЕМЫ ───────────

    @staticmethod
    def _detect_regime(candles: list[dict]) -> dict:
        """Определить режим рынка по свечам."""
        if len(candles) < 21:
            return {"label": "unknown", "icon": "❓", "trend": "neutral", "vol_pct": 0, "atr_pct": 0}

        closes = [_cf(c, "close") for c in candles]
        highs = [_cf(c, "high") for c in candles]
        lows = [_cf(c, "low") for c in candles]
        volumes = [_cf(c, "volume") for c in candles]

        current_price = closes[-1]
        sma20 = _sma(closes, 20) or current_price
        sma50 = _sma(closes, 50) if len(closes) >= 50 else sma20

        # Trend
        sma20_5ago = _sma(closes[:-4], 20) if len(closes) > 24 else sma20
        trend = "neutral"
        if current_price > sma20 * 1.02 and (sma20_5ago is None or sma20 > sma20_5ago * 1.001):
            trend = "uptrend"
        elif current_price < sma20 * 0.98 and (sma20_5ago is None or sma20 < sma20_5ago * 0.999):
            trend = "downtrend"

        # ATR
        atr_val = _atr(candles, 14)
        atr_pct = (atr_val / current_price * 100) if atr_val and current_price > 0 else 0

        # Volume spike
        vol_20 = _sma(volumes, 20) or 1
        vol_last = sum(volumes[-3:]) / 3
        vol_pct = vol_last / vol_20

        # Close position in recent range
        if len(highs) >= 20 and len(lows) >= 20:
            range20 = max(highs[-20:]) - min(lows[-20:])
            pos_in_range = (current_price - min(lows[-20:])) / range20 * 100 if range20 > 0 else 50
        else:
            pos_in_range = 50

        # Определяем режим
        if atr_pct > 3 and vol_pct > 2.5:
            label = "Panic" if pos_in_range < 30 else "Euphoria"
            icon = "💀" if pos_in_range < 30 else "🚀"
        elif atr_pct > 2:
            label = "High Volatility"
            icon = "⚡"
        elif atr_pct < 0.5 and vol_pct < 0.8:
            label = "Low Volatility"
            icon = "💤"
        elif trend == "uptrend":
            label = "Uptrend"
            icon = "📈"
        elif trend == "downtrend":
            label = "Downtrend"
            icon = "📉"
        else:
            label = "Flat / Ranging"
            icon = "➡️"

        return {
            "label": label,
            "icon": icon,
            "trend": trend,
            "atr_pct": round(atr_pct, 2),
            "vol_pct": round(vol_pct, 2),
        }

    @staticmethod
    def _score_volume(candles: list[dict]) -> tuple[str, float, str, str]:
        """Volume factor: 0-25 баллов."""
        volumes = [_cf(c, "volume") for c in candles[-10:]]
        closes = [_cf(c, "close") for c in candles[-10:]]
        opens = [_cf(c, "open") for c in candles[-10:]]
        if not volumes or len(volumes) < 10:
            return ("Volume", 0, "neutral", "📊")

        avg_vol = statistics.mean(volumes)
        if avg_vol == 0:
            return ("Volume", 0, "neutral", "📊")

        last_vol = volumes[-1]
        if len(volumes) >= 3:
            last_3 = sum(volumes[-3:]) / 3
        else:
            last_3 = last_vol

        vol_ratio = last_3 / avg_vol

        # Z-score
        stdev = statistics.stdev(volumes) if len(volumes) > 1 else avg_vol * 0.1
        z = (last_vol - avg_vol) / stdev if stdev > 0 else 0

        # Direction from last 3 candles
        last3_bull = sum(1 for i in range(-3, 0) if closes[i] > opens[i])
        direction = "buy" if last3_bull >= 2 else ("sell" if last3_bull <= 1 else "neutral")

        score = 0
        if z > 2.5:
            score = 25
        elif z > 2.0:
            score = 20
        elif z > 1.5:
            score = 15
        elif vol_ratio > 1.5:
            score = 10
        elif vol_ratio > 1.2:
            score = 5

        label = f"Volume ({vol_ratio:.1f}×, z={z:.1f})"
        return (label, score, direction, "📊")

    @staticmethod
    def _score_whale(whales: list[dict] | None) -> tuple[str, float, str, str] | None:
        """Whale factor: 0-25 баллов."""
        if not whales:
            return None

        now = time.time()
        recent = [t for t in whales if now - t.get("ts", now) < 1800]

        if not recent:
            return None

        buy_vol = sum(t.get("notional", 0) for t in recent if t.get("side") == "buy")
        sell_vol = sum(t.get("notional", 0) for t in recent if t.get("side") == "sell")
        total_vol = buy_vol + sell_vol

        buy_count = sum(1 for t in recent if t.get("side") == "buy")
        sell_count = sum(1 for t in recent if t.get("side") == "sell")

        if total_vol < 50_000:
            return None

        # Score
        score = min(25, total_vol / 50_000)
        direction = "buy" if buy_vol > sell_vol else "sell"

        big_buys = sum(1 for t in recent if t.get("side") == "buy" and t.get("notional", 0) > 250_000)
        if big_buys >= 3:
            score = max(score, 22)

        label = f"Whale (${buy_vol/1000:.0f}K / ${sell_vol/1000:.0f}K)"
        icon = "🐋"
        return (label, score, direction, icon)

    @staticmethod
    def _score_cvd(cvd: float | None) -> tuple[str, float, str, str] | None:
        """CVD factor: 0-20 баллов."""
        if cvd is None or cvd == 0:
            return None
        cvd_abs = abs(cvd)
        direction = "buy" if cvd > 0 else "sell"
        if cvd_abs > 1_000_000:
            score = 20
        elif cvd_abs > 500_000:
            score = 15
        elif cvd_abs > 200_000:
            score = 10
        elif cvd_abs > 100_000:
            score = 5
        else:
            return None
        label = f"CVD ({'+' if cvd > 0 else ''}{cvd/1000:.0f}K)"
        return (label, score, direction, "📈")

    @staticmethod
    def _score_orderbook(ob) -> tuple[str, float, str, str] | None:
        """Orderbook factor: 0-20 баллов."""
        if ob is None:
            return None
        score = 0.0

        imbalance = ob.imbalance_ratio
        if imbalance == float("inf"):
            imbalance = 10.0

        if imbalance > 3.0:
            score += 12
            direction = "buy"
        elif imbalance < 0.33:
            score += 12
            direction = "sell"
        elif imbalance > 2.0:
            score += 6
            direction = "buy"
        elif imbalance < 0.5:
            score += 6
            direction = "sell"
        else:
            direction = "neutral"

        # Стены
        walls = ob.detect_walls(threshold_ratio=5.0)
        bid_walls = walls.get("bid_walls", [])
        ask_walls = walls.get("ask_walls", [])
        if bid_walls:
            score += 5
        if ask_walls:
            score += 5

        # Айсберг
        if hasattr(ob, "detect_iceberg") and ob.detect_iceberg(levels_similar=3):
            score += 3

        if score == 0:
            return None

        spy = ob.spread / ob.best_bid * 100 if ob.best_bid else 0
        label = f"Orderbook (imbalance {1/imbalance:.1f}×)" if imbalance > 1 and imbalance != float("inf") else f"Orderbook (imbalance {imbalance:.1f}×)"
        return (label, score, direction, "📖")

    @staticmethod
    def _score_liquidation(liqs: list[dict] | None) -> tuple[str, float, str, str] | None:
        """Liquidation factor: 0-20 баллов."""
        if not liqs or len(liqs) < 3:
            return None

        total = sum(l.get("notional", 0) for l in liqs)
        if total < 100_000:
            return None

        sell_vol = sum(l.get("notional", 0) for l in liqs if l.get("side") == "sell")
        buy_vol = sum(l.get("notional", 0) for l in liqs if l.get("side") == "buy")

        direction = "buy" if sell_vol > buy_vol * 1.5 else ("sell" if buy_vol > sell_vol * 1.5 else "neutral")

        if total > 3_000_000:
            score = 20
        elif total > 1_500_000:
            score = 15
        elif total > 500_000:
            score = 10
        else:
            score = 5

        label = f"Liq (${sell_vol/1000:.0f}K sell / ${buy_vol/1000:.0f}K buy)"
        return (label, score, direction, "💀")

    @staticmethod
    def _score_momentum(candles: list[dict]) -> tuple[str, float, str, str] | None:
        """Momentum factor: 0-10 баллов."""
        if len(candles) < 6:
            return None

        closes = [_cf(c, "close") for c in candles[-6:]]
        opens = [_cf(c, "open") for c in candles[-6:]]

        green = sum(1 for i in range(6) if closes[i] > opens[i])
        red = 6 - green

        if green >= 5:
            score = 10
            direction = "buy"
        elif green >= 4:
            score = 6
            direction = "buy"
        elif red >= 5:
            score = 10
            direction = "sell"
        elif red >= 4:
            score = 6
            direction = "sell"
        else:
            return None

        label = f"Momentum ({green}G/{red}R)"
        return (label, score, direction, "🔥")

    @staticmethod
    def _get_tf_direction(candles: list[dict] | None, lookback: int = 5) -> dict:
        """Определить направление для набора свечей (таймфрейма).
        Возвращает {direction, strength}."""
        if not candles or len(candles) < lookback:
            return {"direction": "neutral", "strength": 0}

        closes = [_cf(c, "close") for c in candles[-lookback:]]
        opens = [_cf(c, "open") for c in candles[-lookback:]]
        highs = [_cf(c, "high") for c in candles[-lookback:]]
        lows = [_cf(c, "low") for c in candles[-lookback:]]

        # Соотношение зелёных/красных свечей
        green = sum(1 for i in range(lookback) if closes[i] > opens[i])
        red = lookback - green

        # Средняя цена
        avg_price = statistics.mean(closes)

        # Momentum: last vs first close
        mom = closes[-1] - closes[0]
        mom_pct = mom / (closes[0] or 1) * 100

        # Direction
        if green >= 4 and mom_pct > 0:
            direction = "buy"
        elif red >= 4 and mom_pct < 0:
            direction = "sell"
        elif green >= 3 and mom_pct > 0.3:
            direction = "buy"
        elif red >= 3 and mom_pct < -0.3:
            direction = "sell"
        else:
            direction = "neutral"

        # Strength 0-5
        strength = max(green, red) - 2 if direction != "neutral" else 0
        strength = max(0, min(5, strength))

        return {"direction": direction, "strength": strength}

    @staticmethod
    def _score_confluence(ctx: SignalContext) -> dict | None:
        """Multi-Timeframe Confluence.
        Проверяет согласованность направлений на 1m, 5m, 15m.
        Возвращает {stars, direction, details} или None если данных нет."""
        tfs = [
            ("1m", ctx.candles, 5),
            ("5m", ctx.candles_5m, 4),
            ("15m", ctx.candles_15m, 3),
        ]

        results = []
        for label, candles, lookback in tfs:
            res = AIScoreSignal._get_tf_direction(candles, lookback)
            results.append({"tf": label, **res})

        # Считаем сколько таймфреймов смотрят в одну сторону
        buys = [r for r in results if r["direction"] == "buy"]
        sells = [r for r in results if r["direction"] == "sell"]
        neutrals = [r for r in results if r["direction"] == "neutral"]

        if len(buys) >= 2:
            direction = "buy"
            stars = len(buys)
        elif len(sells) >= 2:
            direction = "sell"
            stars = len(sells)
        elif len(buys) == 1 and len(neutrals) >= 1:
            direction = "buy"
            stars = 1
        elif len(sells) == 1 and len(neutrals) >= 1:
            direction = "sell"
            stars = 1
        else:
            direction = None
            stars = 0

        if stars == 0:
            return None

        return {
            "stars": stars,
            "direction": direction,
            "details": [{"tf": r["tf"], "dir": r["direction"], "str": r["strength"]} for r in results],
        }
