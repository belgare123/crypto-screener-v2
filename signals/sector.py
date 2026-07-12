"""Sector signals: ротация, лидеры секторов."""

from __future__ import annotations

from signals.base import BaseSignal, SignalContext, register
from core.sector_scanner import get_sector_engine
from core.relative_strength import get_rs_engine


# ──────────────────────────────────────────────
#  Sector Rotation — смена лидирующего сектора
# ──────────────────────────────────────────────

@register(
    name="sector_rotation",
    description="Смена лидирующего сектора рынка по RS 5m",
    category="sector",
    default_score=60,
    cooldown=1800,
)
class SectorRotationSignal(BaseSignal):
    """Когда лидирующий сектор меняется — сигнал ротации капитала."""

    async def check(self, ctx: SignalContext) -> dict | None:
        rs_engine = get_rs_engine()
        sector_engine = get_sector_engine()
        if rs_engine is None or sector_engine is None:
            return None

        top, _ = rs_engine.get_ranking("rs_5m_pct", top_n=9)
        if not top:
            return None

        snap = sector_engine.scan(top)

        if not snap.rotation_detected:
            return None

        leading = snap.leading_sector
        lagging = snap.lagging_sector

        score = 65
        direction = "buy" if snap.sectors.get(leading, {}).get("direction") == "up" else "neutral"

        return self._result(
            symbol=ctx.symbol if ctx else "",
            score=score,
            direction=direction,
            meta={
                "leading_sector": leading,
                "lagging_sector": lagging,
                "sectors": {
                    s: {
                        "avg_rs_5m": d["avg_rs_5m"],
                        "top": d["top_member"],
                        "direction": d["direction"],
                    }
                    for s, d in snap.sectors.items()
                },
                "type": "sector_rotation",
                "note": f"Ротация: {leading} → лидер | {lagging} → аутсайдер",
            },
        )


# ──────────────────────────────────────────────
#  Sector Strength — сводка топ-секторов
# ──────────────────────────────────────────────

@register(
    name="sector_strength",
    description="Топ-3 и дно-3 сектора по RS 5m (без ротации — раз в 30мин)",
    category="sector",
    default_score=50,
    cooldown=3600,
)
class SectorStrengthSignal(BaseSignal):
    """Сводка сильнейших и слабейших секторов."""

    async def check(self, ctx: SignalContext) -> dict | None:
        rs_engine = get_rs_engine()
        sector_engine = get_sector_engine()
        if rs_engine is None or sector_engine is None:
            return None

        top, _ = rs_engine.get_ranking("rs_5m_pct", top_n=9)
        if not top:
            return None

        snap = sector_engine.scan(top)
        if not snap.sectors:
            return None

        sorted_sectors = sorted(
            snap.sectors.items(),
            key=lambda kv: kv[1]["avg_rs_5m"],
            reverse=True,
        )
        top3 = [s for s, _ in sorted_sectors[:3]]
        bottom3 = [s for s, _ in sorted_sectors[-3:]]
        bottom3.reverse()

        return self._result(
            symbol=ctx.symbol if ctx else "",
            score=50,
            direction="neutral",
            meta={
                "top_sectors": [
                    {"name": s, "avg_rs_5m": snap.sectors[s]["avg_rs_5m"], "top_member": snap.sectors[s]["top_member"]}
                    for s in top3
                ],
                "bottom_sectors": [
                    {"name": s, "avg_rs_5m": snap.sectors[s]["avg_rs_5m"], "top_member": snap.sectors[s]["top_member"]}
                    for s in bottom3
                ],
                "type": "sector_strength",
                "note": f"Сильные: {', '.join(top3)} | Слабые: {', '.join(bottom3)}",
            },
        )
