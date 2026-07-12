"""Volume Spike Signal — аномальный рост объёма (адаптивный порог)."""

from __future__ import annotations

import statistics

from signals.base import BaseSignal, SignalContext, register
from core.adaptive import get_adaptive_thresholds


@register(
    name="volume_spike",
    description="Аномальный рост объёма (Z-score адаптивный, от 2.0 до 6.0 в зависимости от волатильности)",
    category="volume",
    default_score=65,
    cooldown=1800,
    timeframes=["5m", "15m", "1h"],
)
class VolumeSpikeSignal(BaseSignal):
    """
    Ищет свечи с аномальным объёмом.
    Порог Z-score адаптируется под волатильность и сессию:
      - Ночь / штиль: z > 4-5 (только экстремальные всплески)
      - Норма: z > 3
      - Новости / высокая волатильность: z > 2 (не пропустить важное)
    """

    LOOKBACK = 50  # сколько свечей для расчёта базы

    async def check(self, ctx: SignalContext) -> dict | None:
        candles = ctx.candles
        if len(candles) < self.LOOKBACK + 1:
            return None

        volumes = []
        for c in candles[-self.LOOKBACK:-1]:
            try:
                v = float(c.get("volume", c.get("v", 0)))
                volumes.append(v)
            except (ValueError, TypeError):
                volumes.append(0.0)

        try:
            current_vol = float(candles[-1].get("volume", candles[-1].get("v", 0)))
        except (ValueError, TypeError):
            current_vol = 0.0

        if not volumes or current_vol == 0:
            return None

        try:
            mean = statistics.mean(volumes)
            stdev = statistics.stdev(volumes) if len(volumes) > 1 else 0.0
        except statistics.StatisticsError:
            return None

        if stdev == 0:
            return None

        z_score = (current_vol - mean) / stdev

        # ─── Адаптивный порог ───
        adaptive = get_adaptive_thresholds()
        threshold = adaptive.get("volume_z", symbol=ctx.symbol)

        if z_score < threshold.value:
            return None

        # Score: 0-100 пропорционально z-score с учётом порога
        score = min(100, 40 + (z_score - threshold.value) * 10)

        return self._result(
            symbol=ctx.symbol,
            score=score,
            direction="neutral",
            meta={
                "z_score": round(z_score, 2),
                "volume": round(current_vol, 2),
                "avg_volume": round(mean, 2),
                "multiplier": round(current_vol / mean, 1),
                "threshold": round(threshold.value, 2),
                "regime": threshold.regime,
            },
        )
