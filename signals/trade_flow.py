"""Trade flow signals: Buy/Sell Ratio, Taker Flow, Cluster Buy, Whale Accumulation."""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register


# ──────────────────────────────────────────────
#  BuySellRatio — агрессивные buy vs sell за N мин
# ──────────────────────────────────────────────

@register(
    name="buy_sell_ratio",
    description="Агрессивные buy/sell: соотношение > 2x = доминирование",
    category="trade_flow",
    default_score=55,
    cooldown=1800,
)
class BuySellRatioSignal(BaseSignal):
    RATIO_BUY = 2.0
    RATIO_SELL = 0.5

    async def check(self, ctx: SignalContext) -> dict | None:
        whales = ctx.whale_trades
        if not whales or len(whales) < 5:
            return None

        buy_vol = sum(t.get("notional", 0) for t in whales if t.get("side") == "buy")
        sell_vol = sum(t.get("notional", 0) for t in whales if t.get("side") == "sell")

        if buy_vol == 0 or sell_vol == 0:
            return None

        ratio = buy_vol / sell_vol

        if ratio > self.RATIO_BUY:
            return self._result(
                symbol=ctx.symbol, score=65, direction="buy",
                meta={"buy_sell_ratio": round(ratio, 2), "buy_vol": round(buy_vol, 2), "sell_vol": round(sell_vol, 2)},
            )
        if ratio < self.RATIO_SELL:
            return self._result(
                symbol=ctx.symbol, score=60, direction="sell",
                meta={"buy_sell_ratio": round(ratio, 2), "buy_vol": round(buy_vol, 2), "sell_vol": round(sell_vol, 2)},
            )
        return None


# ──────────────────────────────────────────────
#  TakerFlow — рыночные buy/sell объём
# ──────────────────────────────────────────────

@register(
    name="taker_flow",
    description="Рыночные buy объём > sell = агрессия покупателей",
    category="trade_flow",
    default_score=50,
    cooldown=1800,
)
class TakerFlowSignal(BaseSignal):
    MIN_SPREAD = 50_000

    async def check(self, ctx: SignalContext) -> dict | None:
        whales = ctx.whale_trades
        if not whales or len(whales) < 5:
            return None

        buy_vol = sum(t.get("notional", 0) for t in whales if t.get("side") == "buy")
        sell_vol = sum(t.get("notional", 0) for t in whales if t.get("side") == "sell")
        total = buy_vol + sell_vol

        if total < self.MIN_SPREAD:
            return None

        net_flow = (buy_vol - sell_vol) / total * 100  # в %

        if abs(net_flow) < 20:
            return None

        direction = "buy" if net_flow > 0 else "sell"
        score = min(75, 50 + abs(net_flow) / 2)
        return self._result(
            symbol=ctx.symbol, score=score, direction=direction,
            meta={"net_flow_pct": round(net_flow, 1), "buy_vol": round(buy_vol, 2), "sell_vol": round(sell_vol, 2)},
        )


# ──────────────────────────────────────────────
#  ClusterBuy — N+ китовых buy за M минут
# ──────────────────────────────────────────────

@register(
    name="cluster_buy",
    description="3+ крупных buy за 5 минут = накопление",
    category="trade_flow",
    default_score=60,
    cooldown=3600,
)
class ClusterBuySignal(BaseSignal):
    MIN_CLUSTER = 3
    CLUSTER_WINDOW = 300  # 5 мин

    async def check(self, ctx: SignalContext) -> dict | None:
        whales = ctx.whale_trades
        if not whales or len(whales) < self.MIN_CLUSTER:
            return None

        import time
        now = time.time()

        def _in_window(trades, side, window):
            return [t for t in trades if t.get("side") == side and now - t.get("ts", now) < window]

        buy_cluster = _in_window(whales, "buy", self.CLUSTER_WINDOW)
        sell_cluster = _in_window(whales, "sell", self.CLUSTER_WINDOW)

        if len(buy_cluster) >= self.MIN_CLUSTER:
            total = sum(t.get("notional", 0) for t in buy_cluster)
            score = min(80, 55 + len(buy_cluster) * 5)
            return self._result(
                symbol=ctx.symbol, score=score, direction="buy",
                meta={"cluster_count": len(buy_cluster), "total_notional": round(total, 2)},
            )
        if len(sell_cluster) >= self.MIN_CLUSTER:
            total = sum(t.get("notional", 0) for t in sell_cluster)
            score = min(80, 55 + len(sell_cluster) * 5)
            return self._result(
                symbol=ctx.symbol, score=score, direction="sell",
                meta={"cluster_count": len(sell_cluster), "total_notional": round(total, 2)},
            )
        return None


# ──────────────────────────────────────────────
#  WhaleAccum — мелкие buy + крупная sell = дистрибуция
# ──────────────────────────────────────────────

@register(
    name="whale_accum",
    description="Много мелких buy + одна крупная sell = дистрибуция",
    category="trade_flow",
    default_score=60,
    cooldown=3600,
)
class WhaleAccumSignal(BaseSignal):
    BIG_THRESHOLD = 250_000
    SMALL_THRESHOLD = 50_000

    async def check(self, ctx: SignalContext) -> dict | None:
        whales = ctx.whale_trades
        if not whales or len(whales) < 5:
            return None

        import time
        now = time.time()
        recent = [t for t in whales if now - t.get("ts", now) < 3600]
        if len(recent) < 5:
            return None

        big_trades = [t for t in recent if t.get("notional", 0) >= self.BIG_THRESHOLD]
        small_trades = [t for t in recent if t.get("notional", 0) < self.BIG_THRESHOLD]

        if not big_trades:
            return None

        # Дистрибуция: много мелких buy + одна крупная sell
        small_buy = sum(t.get("notional", 0) for t in small_trades if t.get("side") == "buy")
        small_sell = sum(t.get("notional", 0) for t in small_trades if t.get("side") == "sell")
        big_buy = sum(t.get("notional", 0) for t in big_trades if t.get("side") == "buy")
        big_sell = sum(t.get("notional", 0) for t in big_trades if t.get("side") == "sell")

        # Дистрибуция: мелкие накапливают buy, крупный продаёт
        if small_buy > small_sell * 2 and big_sell > big_buy * 2:
            score = 70
            return self._result(
                symbol=ctx.symbol, score=score, direction="sell",
                meta={"small_buy": round(small_buy, 2), "big_sell": round(big_sell, 2)},
            )
        # Накопление: мелкие продают, крупный покупает
        if small_sell > small_buy * 2 and big_buy > big_sell * 2:
            score = 65
            return self._result(
                symbol=ctx.symbol, score=score, direction="buy",
                meta={"small_sell": round(small_sell, 2), "big_buy": round(big_buy, 2)},
            )
        return None
