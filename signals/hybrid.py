"""Hybrid signals combining 2+ data sources: WallBounce, WhaleWall, CVDDivergence, BidAskRatchet."""

from __future__ import annotations

import statistics

from signals.base import BaseSignal, SignalContext, register


# ──────────────────────────────────────────────
#  WallBounce — bid wall + sell liquidation = отскок
# ──────────────────────────────────────────────

@register(
    name="wall_bounce",
    description="Bid стена + sell ликвидации = поддержка, отскок",
    category="hybrid",
    default_score=70,
    cooldown=7200,
)
class WallBounceSignal(BaseSignal):
    async def check(self, ctx: SignalContext) -> dict | None:
        ob = ctx.orderbook
        liqs = ctx.liquidations
        if ob is None or not liqs or len(liqs) < 3:
            return None

        walls = ob.detect_walls(threshold_ratio=5.0)
        bid_walls = walls.get("bid_walls", [])

        if not bid_walls:
            return None

        # Есть sell-ликвидации (long squeeze)?
        sell_liq_vol = sum(l.get("notional", 0) for l in liqs if l.get("side") == "sell")
        if sell_liq_vol < 100_000:
            return None

        wall_price = max(bid_walls, key=lambda w: w[1])[0]
        current_price = ob.best_bid
        if not current_price:
            return None

        # Расстояние до стены < 1% = близко
        dist_pct = abs(current_price - wall_price) / current_price * 100
        if dist_pct > 1.0:
            return None

        score = 75
        return self._result(
            symbol=ctx.symbol, score=score, direction="buy",
            meta={"wall_price": wall_price, "sell_liq_vol": round(sell_liq_vol, 2),
                  "dist_pct": round(dist_pct, 2)},
        )


# ──────────────────────────────────────────────
#  WhaleWall — whale buy рядом с ask стеной
# ──────────────────────────────────────────────

@register(
    name="whale_wall",
    description="Кит покупает у ask-стены = агрессивное накопление",
    category="hybrid",
    default_score=65,
    cooldown=7200,
)
class WhaleWallSignal(BaseSignal):
    async def check(self, ctx: SignalContext) -> dict | None:
        ob = ctx.orderbook
        whales = ctx.whale_trades
        if ob is None or not whales:
            return None

        walls = ob.detect_walls(threshold_ratio=5.0)
        ask_walls = walls.get("ask_walls", [])

        if not ask_walls:
            return None

        # Крупные buy-сделки
        buy_whales = [t for t in whales if t.get("side") == "buy" and t.get("notional", 0) > 100_000]
        if not buy_whales:
            return None

        # Проверяем, что киты покупают вблизи ask-стены
        lowest_ask_wall = min(ask_walls, key=lambda w: w[0])[0]
        avg_buy_price = statistics.mean(t.get("price", 0) for t in buy_whales)

        dist_pct = abs(avg_buy_price - lowest_ask_wall) / avg_buy_price * 100 if avg_buy_price > 0 else 999
        if dist_pct > 0.5:  # в пределах 0.5% от стены
            return None

        score = 70
        return self._result(
            symbol=ctx.symbol, score=score, direction="buy",
            meta={"ask_wall_price": lowest_ask_wall, "avg_buy_price": round(avg_buy_price, 2),
                  "whale_count": len(buy_whales), "dist_pct": round(dist_pct, 2)},
        )


# ──────────────────────────────────────────────
#  CVDDivergence — цена вверх, CVD вниз = разворот
# ──────────────────────────────────────────────

@register(
    name="cvd_divergence",
    description="Цена растёт, CVD падает = дивергенция, разворот",
    category="hybrid",
    default_score=65,
    cooldown=7200,
)
class CVDDivergenceSignal(BaseSignal):
    async def check(self, ctx: SignalContext) -> dict | None:
        candles = ctx.candles
        cvd = ctx.cvd
        if len(candles) < 5 or cvd == 0:
            return None

        def _fc(c, k):
            v = c.get(k)
            return float(v) if v is not None else 0.0

        # Тренд цены за последние 5 свечей
        price_start = _fc(candles[-5], "close")
        price_now = _fc(candles[-1], "close")
        price_change = (price_now - price_start) / price_start * 100

        if abs(price_change) < 0.3:
            return None

        # Цена растёт, CVD отрицательный = медвежья дивергенция
        if price_change > 0 and cvd < -100_000:
            score = 70
            direction = "sell"
        # Цена падает, CVD положительный = бычья дивергенция
        elif price_change < 0 and cvd > 100_000:
            score = 65
            direction = "buy"
        else:
            return None

        return self._result(
            symbol=ctx.symbol, score=score, direction=direction,
            meta={"price_change_pct": round(price_change, 2), "cvd": round(cvd, 2)},
        )


# ──────────────────────────────────────────────
#  BidAskRatchet — цена поднимается, но ask объём растёт
# ──────────────────────────────────────────────

@register(
    name="bid_ask_ratchet",
    description="Цена вверх, ask-объём растёт = скрытое сопротивление",
    category="hybrid",
    default_score=55,
    cooldown=3600,
)
class BidAskRatchetSignal(BaseSignal):
    RATCHET_THRESHOLD = 1.3

    async def check(self, ctx: SignalContext) -> dict | None:
        ob = ctx.orderbook
        candles = ctx.candles
        if ob is None or len(candles) < 3:
            return None

        def _fc(c, k):
            v = c.get(k)
            return float(v) if v is not None else 0.0

        # Цена растёт?
        price_up = _fc(candles[-1], "close") > _fc(candles[-3], "close")
        price_down = _fc(candles[-1], "close") < _fc(candles[-3], "close")

        if not price_up and not price_down:
            return None

        # Смотрим отношение верхних ask к нижним bid
        ask_top = sum(a[1] for a in ob.asks[:10])
        bid_top = sum(b[1] for b in ob.bids[:10])

        if bid_top == 0:
            return None
        ratio = ask_top / bid_top

        # Цена растёт, ask объём > bid объём = скрытые продавцы
        if price_up and ratio > self.RATCHET_THRESHOLD:
            score = 60
            return self._result(
                symbol=ctx.symbol, score=score, direction="sell",
                meta={"ask_bid_ratio": round(ratio, 2), "ask_vol_top10": round(ask_top, 2),
                      "bid_vol_top10": round(bid_top, 2)},
            )
        # Цена падает, bid объём > ask объём = скрытые покупатели
        if price_down and ratio > 0 and 1 / ratio > self.RATCHET_THRESHOLD:
            score = 55
            return self._result(
                symbol=ctx.symbol, score=score, direction="buy",
                meta={"ask_bid_ratio": round(ratio, 2), "ask_vol_top10": round(ask_top, 2),
                      "bid_vol_top10": round(bid_top, 2)},
            )
        return None
