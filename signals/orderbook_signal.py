"""Orderbook Signal — дисбаланс стакана, стенки, спред."""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register


@register(
    name="orderbook",
    description="Дисбаланс стакана: bid/ask ratio > 3 или стены",
    category="orderbook",
    default_score=60,
    cooldown=1800,
)
class OrderbookSignal(BaseSignal):
    """
    Анализирует состояние стакана.
    - Imbalance ratio > 3 = сильный наклон (score 70+)
    - Обнаружены крупные стены (score 60+)
    - Узкий спред + дисбаланс = дополнительный вес
    """

    IMBALANCE_HIGH = 3.0
    IMBALANCE_MED = 1.8

    async def check(self, ctx: SignalContext) -> dict | None:
        ob = ctx.orderbook
        if ob is None:
            return None

        imbalance = ob.imbalance_ratio
        if imbalance == float("inf"):
            imbalance = 10.0

        walls = ob.detect_walls(threshold_ratio=5.0)
        bid_walls = walls.get("bid_walls", [])
        ask_walls = walls.get("ask_walls", [])

        score = 0
        factors = []

        # Imbalance
        if imbalance > self.IMBALANCE_HIGH:
            factors.append((f"Bid-heavy imbalance ({imbalance:.1f}x)", 30))
            score += 30
        elif imbalance > 0 and imbalance < 1.0 / self.IMBALANCE_HIGH:
            factors.append((f"Ask-heavy imbalance ({1/imbalance:.1f}x)", 30))
            score += 30
        elif imbalance > self.IMBALANCE_MED:
            factors.append((f"Moderate imbalance ({imbalance:.1f}x)", 15))
            score += 15
        elif imbalance > 0 and imbalance < 1.0 / self.IMBALANCE_MED:
            factors.append((f"Moderate ask imbalance ({1/imbalance:.1f}x)", 15))
            score += 15

        # Bid walls = поддержка
        if bid_walls:
            biggest = max(bid_walls, key=lambda w: w[1])
            factors.append((f"Bid wall ${biggest[0]:.1f} x{biggest[2]}", 20))
            score += 20

        # Ask walls = сопротивление
        if ask_walls:
            biggest = max(ask_walls, key=lambda w: w[1])
            factors.append((f"Ask wall ${biggest[0]:.1f} x{biggest[2]}", 15))
            score += 15

        if score < 20:
            return None

        # Направление
        imbalance_dir = "bid-heavy" if imbalance > 1.0 else "ask-heavy"
        direction = "buy" if imbalance > 1.2 else ("sell" if imbalance < 0.8 else "neutral")

        return self._result(
            symbol=ctx.symbol,
            score=min(100, score),
            direction=direction,
            meta={
                "imbalance_ratio": round(imbalance, 2),
                "bid_walls": len(bid_walls),
                "ask_walls": len(ask_walls),
                "imbalance_dir": imbalance_dir,
                "spread": round(ob.spread, 2),
                "bid_volume": round(ob.bid_volume, 2),
                "ask_volume": round(ob.ask_volume, 2),
            },
        )
