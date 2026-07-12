"""Advanced liquidation signals: Cascade, SqueezeSetup, MaxPain."""

from __future__ import annotations

import time

from signals.base import BaseSignal, SignalContext, register


# ──────────────────────────────────────────────
#  Cascade — N+ ликвидаций за M секунд (каскад)
# ──────────────────────────────────────────────

@register(
    name="liquidation_cascade",
    description="N+ ликвидаций за 30 секунд = каскад, маржин-колл цепочка",
    category="liquidation",
    default_score=65,
    cooldown=3600,
)
class CascadeSignal(BaseSignal):
    MIN_CASCADE = 5
    CASCADE_WINDOW = 30  # секунд

    async def check(self, ctx: SignalContext) -> dict | None:
        liqs = ctx.liquidations
        if not liqs or len(liqs) < self.MIN_CASCADE:
            return None

        now = time.time()
        recent = [l for l in liqs if now - l.get("ts", now) < self.CASCADE_WINDOW]

        if len(recent) < self.MIN_CASCADE:
            return None

        total_notional = sum(l.get("notional", 0) for l in recent)
        sell_liqs = sum(1 for l in recent if l.get("side") == "sell")

        # Преобладание sell = long squeeze
        sell_ratio = sell_liqs / len(recent)
        direction = "buy" if sell_ratio > 0.6 else ("sell" if sell_ratio < 0.4 else "neutral")
        score = min(85, 60 + len(recent) * 3)

        return self._result(
            symbol=ctx.symbol, score=score, direction=direction,
            meta={"cascade_count": len(recent), "total_notional": round(total_notional, 2),
                  "sell_ratio": round(sell_ratio, 2)},
        )


# ──────────────────────────────────────────────
#  SqueezeSetup — long-ликвидации, затем отскок
# ──────────────────────────────────────────────

@register(
    name="squeeze_setup",
    description="Длинные ликвидации + цена отскочила = short squeeze готовится",
    category="liquidation",
    default_score=70,
    cooldown=7200,
)
class SqueezeSetupSignal(BaseSignal):
    async def check(self, ctx: SignalContext) -> dict | None:
        liqs = ctx.liquidations
        if not liqs:
            return None

        # Делим ликвидации на 2 половины: старые и свежие
        import statistics
        mid_ts = sum(l.get("ts", 0) for l in liqs) / len(liqs) if liqs else 0

        older = [l for l in liqs if l.get("ts", 0) < mid_ts]
        newer = [l for l in liqs if l.get("ts", 0) >= mid_ts]

        if len(older) < 3 or len(newer) < 3:
            return None

        older_sell = sum(l.get("notional", 0) for l in older if l.get("side") == "sell")
        older_buy = sum(l.get("notional", 0) for l in older if l.get("side") == "buy")
        newer_sell = sum(l.get("notional", 0) for l in newer if l.get("side") == "sell")
        newer_buy = sum(l.get("notional", 0) for l in newer if l.get("side") == "buy")

        older_total = older_sell + older_buy
        newer_total = newer_sell + newer_buy
        if older_total == 0 or newer_total == 0:
            return None

        # Старые: много sell (long squeeze)
        if not (older_sell > older_buy * 2):
            return None

        # Свежие: buy возвращаются (отскок)
        if not (newer_buy > newer_sell):
            return None

        score = 75
        return self._result(
            symbol=ctx.symbol, score=score, direction="buy",
            meta={"older_sell_ratio": round(older_sell / older_total, 2),
                  "newer_buy_ratio": round(newer_buy / newer_total, 2)},
        )


# ──────────────────────────────────────────────
#  MaxPain — скопление ликвидаций на уровне = зона разворота
# ──────────────────────────────────────────────

@register(
    name="liquidation_cluster",
    description="Скопление ликвидаций на одной цене = зона максимальной боли",
    category="liquidation",
    default_score=65,
    cooldown=7200,
)
class MaxPainSignal(BaseSignal):
    async def check(self, ctx: SignalContext) -> dict | None:
        liqs = ctx.liquidations
        if not liqs or len(liqs) < 5:
            return None

        # Группируем по цене (округляем до 0.5%)
        price_groups: dict[str, list[float]] = {}
        for l in liqs:
            price = l.get("price", 0)
            if price == 0:
                continue
            bucket = round(price * 2) / 2  # округление до 0.5
            key = f"{bucket:.2f}"
            if key not in price_groups:
                price_groups[key] = []
            price_groups[key].append(l.get("notional", 0))

        if not price_groups:
            return None

        # Ищем уровень с максимальным объёмом ликвидаций
        max_level = max(price_groups, key=lambda k: sum(price_groups[k]))
        max_vol = sum(price_groups[max_level])
        total_vol = sum(sum(v) for v in price_groups.values())

        if max_vol < total_vol * 0.3:  # кластер < 30% от всех
            return None

        price = float(max_level)
        if not ctx.candles:
            return None

        current_price = float(ctx.candles[-1].get("close", 0))
        if current_price <= 0:
            return None

        MAX_DIST_PCT = 5.0
        dist_pct = abs(current_price - price) / current_price * 100
        if dist_pct > MAX_DIST_PCT:
            return None

        direction = "buy" if price < current_price else "sell"
        score = 70 if dist_pct < 2 else 60

        return self._result(
            symbol=ctx.symbol, score=score, direction=direction,
            meta={"cluster_price": max_level, "cluster_vol": round(max_vol, 2),
                  "cluster_share": round(max_vol / total_vol * 100, 1),
                  "distance_pct": round(dist_pct, 2)},
        )
