"""
NoiseDetector — определяет, насколько рынок «шумный».

Измеряет три компонента:
- Тело/фитиль свечей: маленькое тело + длинные тени = шум
- Направленность: сколько свечей подряд в одном направлении
- Волатильность: ATR% vs медианный ATR% (из VolatilityFeatureCalculator)

Выход:
- label: low / normal / high
- score: 0-100 (100 = максимально шумно)
- is_noisy: True если score > 70 (можно подавить сигналы)
"""

from __future__ import annotations

import logging
import math

from core.state.market_state import NoiseDimension

logger = logging.getLogger(__name__)


# ── Пороги ──

NOISE_THRESHOLD_PCT = 70       # % — считаем рынок шумным
DIRECTIONAL_WINDOW = 8         # свечей для проверки направленности
BODY_TO_WICK_MIN = 0.3         # min отношение тела к свече (иначе шум)
VOLATILITY_NOISE_MULT = 1.5    # множитель ATR% для шумового порога


class NoiseDetector:
    """
    Определяет шумность рынка.

    Usage:
        detector = NoiseDetector()
        noise = await detector.detect("BTC/USDT:USDT", candles)
    """

    async def detect(
        self,
        symbol: str,
        candles: list[dict] | None = None,
    ) -> NoiseDimension:
        """Определить шумность рынка по свечам."""
        if not candles or len(candles) < 5:
            return NoiseDimension(label="normal", score=50.0)

        recent = candles[-25:]  # используем последние 25 свечей
        n = len(recent)

        # ── 1. Body-to-wick ratio ──
        body_sizes = []
        total_ranges = []
        for c in recent:
            high = float(c.get("high", 0))
            low = float(c.get("low", 0))
            close = float(c.get("close", 0))
            open_ = float(c.get("open", 0))

            body = abs(close - open_)
            total_range = high - low
            body_sizes.append(body)
            total_ranges.append(total_range)

        body_to_wick = 0.0
        if total_ranges and sum(total_ranges) > 0:
            body_to_wick = sum(body_sizes) / sum(total_ranges)

        # ── 2. Directional consistency ──
        directional_count = 0
        for i in range(1, min(len(recent), DIRECTIONAL_WINDOW + 1)):
            c = recent[-i]
            c_prev = recent[-i - 1] if -i - 1 >= -len(recent) else None
            if c_prev:
                up = float(c["close"]) > float(c["open"])
                prev_up = float(c_prev["close"]) > float(c_prev["open"])
                if up == prev_up:
                    directional_count += 1
                else:
                    directional_count = 0

        # ── 3. Range analysis — are candles expanding or chopping? ──
        if len(total_ranges) >= 2:
            half = len(total_ranges) // 2
            recent_half = total_ranges[half:]
            older_half = total_ranges[:half]
            recent_avg = sum(recent_half) / len(recent_half) if recent_half else 0
            older_avg = sum(older_half) / len(older_half) if older_half else 0

            if older_avg > 0:
                range_expansion = recent_avg / older_avg
            else:
                range_expansion = 1.0
        else:
            range_expansion = 1.0

        # ── 4. Range as % of price ──
        last_price = float(recent[-1].get("close", 1))
        avg_range_pct = 0.0
        if last_price > 0 and total_ranges:
            avg_range_pct = (sum(total_ranges) / len(total_ranges)) / last_price * 100

        # ── Композитный score ──
        # Маленькое body_to_wick = шум
        wick_noise = max(0, min(100, (1.0 - body_to_wick / BODY_TO_WICK_MIN) * 100)) if body_to_wick < BODY_TO_WICK_MIN else max(0, (1.0 - body_to_wick) * 20)

        # Мало направленных свечей = шум
        dir_noise = max(0, (1.0 - directional_count / DIRECTIONAL_WINDOW) * 100)

        # Расширение диапазона = меньше шума (если направленное)
        range_in_noise = max(0, (1.0 - range_expansion) * 50) if range_expansion > 1 else 0

        # Среднее
        noise_pct = (wick_noise * 0.4 + dir_noise * 0.4 + range_in_noise * 0.2)

        label = "high" if noise_pct >= NOISE_THRESHOLD_PCT else \
                "low" if noise_pct < 30 else "normal"

        meta = {
            "body_to_wick_ratio": round(body_to_wick, 3),
            "directional_candles": min(directional_count, DIRECTIONAL_WINDOW),
            "range_expansion": round(range_expansion, 2),
            "avg_range_pct": round(avg_range_pct, 3),
            "candles_analyzed": n,
        }

        logger.debug(
            "[noise] %s → %s score=%.0f body/wick=%.3f dir=%d/%d expand=%.2f",
            symbol, label, noise_pct,
            body_to_wick, min(directional_count, DIRECTIONAL_WINDOW),
            DIRECTIONAL_WINDOW, range_expansion,
        )

        return NoiseDimension(
            label=label,
            score=round(noise_pct, 1),
            noise_pct=round(noise_pct, 1),
            directional_candles=min(directional_count, DIRECTIONAL_WINDOW),
            body_to_wick_ratio=round(body_to_wick, 3),
            is_noisy=noise_pct >= NOISE_THRESHOLD_PCT,
            meta=meta,
        )
