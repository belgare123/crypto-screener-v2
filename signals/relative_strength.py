"""RS signals: смена силы/слабости актива, ранжирование."""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core.relative_strength import get_rs_engine


# ──────────────────────────────────────────────
#  RS Momentum — актив резко усилился / ослаб
# ──────────────────────────────────────────────

@register(
    name="rs_momentum",
    description="Актив резко изменил относительную силу против BTC (+/- 5% за 5 минут)",
    category="relative_strength",
    default_score=60,
    cooldown=1800,
)
class RSMomentumSignal(BaseSignal):
    """Когда RS символа резко меняется — признак смены лидера."""

    async def check(self, ctx: SignalContext) -> dict | None:
        rs_engine = get_rs_engine()
        if rs_engine is None:
            return None

        symbol_short = ctx.symbol.split("/")[0]
        if symbol_short == "BTC":
            return None

        _, rs_5m = rs_engine.get_rs(ctx.symbol, lookback=6)
        _, rs_15m = rs_engine.get_rs(ctx.symbol, lookback=16)

        if rs_5m is None or rs_15m is None:
            return None

        # Анализируем RS
        if rs_5m > 5.0:
            # Значительное усиление RS
            score = min(80, 60 + int(abs(rs_5m) * 2))
            return self._result(
                symbol=ctx.symbol, score=score, direction="buy",
                meta={
                    "rs_5m_pct": round(rs_5m, 2),
                    "rs_15m_pct": round(rs_15m, 2),
                    "type": "strengthening",
                    "note": f"RS +{rs_5m:.1f}% за 5мин — {symbol_short} сильнее BTC",
                },
            )
        elif rs_5m < -5.0:
            # Значительное ослабление
            score = min(80, 60 + int(abs(rs_5m) * 2))
            return self._result(
                symbol=ctx.symbol, score=score, direction="sell",
                meta={
                    "rs_5m_pct": round(rs_5m, 2),
                    "rs_15m_pct": round(rs_15m, 2),
                    "type": "weakening",
                    "note": f"RS {rs_5m:.1f}% за 5мин — {symbol_short} слабее BTC",
                },
            )
        return None


# ──────────────────────────────────────────────
#  RS Ranking — кто в топе / дне рынка
# ──────────────────────────────────────────────

@register(
    name="rs_ranking",
    description="Топ-3 актива по Relative Strength (5m)",
    category="relative_strength",
    default_score=65,
    cooldown=1800,
)
class RSRankingSignal(BaseSignal):
    """Топ-3 сильнейших / слабейших по RS."""

    _last_top: list[str] | None = None
    _last_bottom: list[str] | None = None

    async def check(self, ctx: SignalContext) -> dict | None:
        rs_engine = get_rs_engine()
        if rs_engine is None:
            return None

        top, bottom = rs_engine.get_ranking("rs_5m_pct", top_n=3)
        if not top:
            return None

        top_symbols = [r["symbol"].split("/")[0] for r in top]
        bottom_symbols = [r["symbol"].split("/")[0] for r in bottom]

        changed = (
            top_symbols != self._last_top
            or bottom_symbols != self._last_bottom
        )
        self._last_top = top_symbols
        self._last_bottom = bottom_symbols

        if not changed:
            # Топ не изменился — не шлём
            return None

        return self._result(
            symbol=ctx.symbol,
            score=65,
            direction="neutral" if not bottom else "sell",
            meta={
                "top_assets": [
                    {"symbol": r["symbol"].split("/")[0], "rs_5m": r["rs_5m_pct"]} for r in top
                ],
                "bottom_assets": [
                    {"symbol": r["symbol"].split("/")[0], "rs_5m": r["rs_5m_pct"]} for r in bottom
                ],
                "type": "ranking_update",
                "note": f"Топ: {', '.join(top_symbols)} | Дно: {', '.join(bottom_symbols)}",
            },
        )
