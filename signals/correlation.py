"""Correlation signals: лидеры рынка, дивергенции, рассинхронизация."""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core.correlation import get_correlation_engine


# ──────────────────────────────────────────────
#  Market Leader — альт ведёт рынок
# ──────────────────────────────────────────────

@register(
    name="market_leader",
    description="Альт-монета движется раньше BTC/ETH = смена лидера",
    category="correlation",
    default_score=70,
    cooldown=3600,
)
class MarketLeaderSignal(BaseSignal):
    """
    Когда SOL, XRP или другой альт растёт быстрее BTC и ETH,
    это сигнал смены ликвидности.
    """

    async def check(self, ctx: SignalContext) -> dict | None:
        ce = get_correlation_engine()
        info = ce.get_info()

        leaders = info.get("leaders", [])
        if not leaders:
            return None

        # Проверяем: есть ли альт в лидерах, пока BTC/ETH в отстающих?
        symbol_short = ctx.symbol.split("/")[0]
        if symbol_short not in leaders:
            return None

        laggards = info.get("laggards", [])
        btc_eth_laggards = [s for s in laggards if s in ("BTC", "ETH")]

        if btc_eth_laggards and symbol_short not in ("BTC", "ETH"):
            # Альт лидирует, BTC/ETH отстают — смена лидера
            score = 75
            return self._result(
                symbol=ctx.symbol, score=score, direction="buy",
                meta={
                    "leaders": leaders,
                    "laggards": laggards,
                    "alignment": info.get("alignment"),
                    "type": "alt_leads",
                },
            )

        # Альт просто в топе движения
        return self._result(
            symbol=ctx.symbol, score=65, direction="neutral",
            meta={
                "leaders": leaders,
                "alignment": info.get("alignment"),
                "type": "in_leaders",
            },
        )


# ──────────────────────────────────────────────
#  Divergence — рассинхронизация с рынком
# ──────────────────────────────────────────────

@register(
    name="divergence",
    description="Актив рассинхронизировался с рынком (+/- 2σ при низкой корреляции)",
    category="correlation",
    default_score=60,
    cooldown=3600,
)
class DivergenceSignal(BaseSignal):
    """
    Когда актив движется независимо от рынка (z-score > 2 и corr < 0.5).
    Может означать скрытый накоп / раздачу.
    """

    async def check(self, ctx: SignalContext) -> dict | None:
        ce = get_correlation_engine()
        snap = ce.get_snapshot()

        symbol_short = ctx.symbol.split("/")[0]
        divergences = snap.divergences

        if symbol_short not in divergences:
            return None

        z = snap.z_scores.get(ctx.symbol, 0)
        direction = "buy" if z > 0 else "sell"
        score = min(80, 60 + abs(z) * 5)

        return self._result(
            symbol=ctx.symbol, score=score, direction=direction,
            meta={
                "z_score": round(z, 2),
                "divergence_symbols": [s.split("/")[0] for s in divergences],
                "alignment": round(snap.alignment, 2),
                "type": "z_score_divergence",
            },
        )


# ──────────────────────────────────────────────
#  Market Alignment — общий настрой рынка
# ──────────────────────────────────────────────

@register(
    name="market_alignment",
    description="Рынок вошёл в режим сильной/слабой корреляции",
    category="correlation",
    default_score=40,
    cooldown=3600,
)
class MarketAlignmentSignal(BaseSignal):
    """
    Когда alignment меняется:
      - > 0.8: всё скоррелировано = рыночный режим (торгуй BTC)
      - < 0.3: хаос = ищи дивергенции
    """

    async def check(self, ctx: SignalContext) -> dict | None:
        ce = get_correlation_engine()
        snap = ce.get_snapshot()
        info = ce.get_info()

        # Ждём хотя бы 3 пары с данными
        if len(snap.pairs) < 3:
            return None

        alignment = info.get("alignment", 0.5)

        if alignment > 0.8:
            return self._result(
                symbol=ctx.symbol, score=50, direction="neutral",
                meta={
                    "alignment": alignment,
                    "leaders": info.get("leaders"),
                    "state": "highly_correlated",
                    "advice": "Рынок единый — торгуй BTC/ETH",
                },
            )
        if alignment < 0.3:
            return self._result(
                symbol=ctx.symbol, score=55, direction="neutral",
                meta={
                    "alignment": alignment,
                    "divergences": info.get("divergences"),
                    "state": "decoupled",
                    "advice": "Рынок фрагментирован — ищи дивергенции",
                },
            )
        return None
