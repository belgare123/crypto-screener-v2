"""
ParticipationDetector — определяет тип участников рынка.

Определяет, кто движет рынком: розничные трейдеры, институционалы или баланс.

Метрики:
- OI change rate: быстрый рост OI → институциональный вход
- CVD trend: CVD растёт → агрессивные покупки (институционал)
- Whale volume %: доля китовых сделок в общем объёме
- Taker buy/sell ratio: агрессивность покупок
- Liq side ratio: доминирование ликвидаций (buy/sell)

Выход:
- label: retail / balanced / institutional
- score: 0-100 (0 = retail, 50 = balanced, 100 = institutional)
"""

from __future__ import annotations

import logging
import time

from core.state.market_state import ParticipationDimension

logger = logging.getLogger(__name__)


# ── Пороги ──

OI_SURGE_PCT = 5.0         # % роста OI за короткое время
CVD_STRONG_THRESHOLD = 0.6  # направленность CVD (0-1)
WHALE_VOLUME_HIGH = 40.0    # % китовых сделок → институционально
WHALE_VOLUME_LOW = 15.0     # % китовых сделок → розница
TAKER_BUY_RATIO_HIGH = 0.6  # > 60% агрессивных покупок
TAKER_BUY_RATIO_LOW = 0.4   # < 40% агрессивных покупок


class ParticipationDetector:
    """
    Определяет тип участников.

    Usage:
        detector = ParticipationDetector()
        participation = await detector.detect("BTC/USDT:USDT")
    """

    def __init__(self, feature_store=None):
        from core.features.store import get_feature_store
        self._store = feature_store or get_feature_store()

    async def detect(self, symbol: str) -> ParticipationDimension:
        """Определить тип участников через FeatureStore."""
        names = [
            "ticker.volume_24h",
            "liq.volume_5m",
            "liq.side_ratio",
            "liq.count_5m",
        ]
        features = await self._store.get_multi([symbol], names)
        feats = features.get(symbol, {})

        return self._calc_score(
            volume_24h=feats.get("ticker.volume_24h", 0) or 0,
            liq_vol_5m=feats.get("liq.volume_5m", 0) or 0,
            liq_side_ratio=feats.get("liq.side_ratio", 0.5) or 0.5,
            liq_count_5m=feats.get("liq.count_5m", 0) or 0,
        )

    def _calc_score(
        self,
        volume_24h: float = 0,
        liq_vol_5m: float = 0,
        liq_side_ratio: float = 0.5,
        liq_count_5m: int = 0,
        oi_change_pct: float = 0,
        cvd_trend: float = 0,
        whale_volume_pct: float = 0,
        taker_buy_ratio: float = 0.5,
    ) -> ParticipationDimension:
        """Чистая логика расчёта participation (без I/O — для тестов)."""

        # ── 1. Liquidation analysis ──
        # Высокий объём ликвидаций на одной стороне → каскад (розница)
        liq_imbalance = abs(liq_side_ratio - 0.5) * 2  # 0-1
        retail_liq_score = 0
        if liq_imbalance > 0.6 and liq_vol_5m > 0:
            retail_liq_score = min(100, liq_vol_5m * liq_imbalance * 10)

        # ── 2. Volume analysis (proxy) ──
        # Высокий объём + низкая волатильность → институциональное накопление

        # ── 3. CVD + OI analysis (if available) ──
        # oi_change_pct, cvd_trend, whale_volume_pct, taker_buy_ratio
        # Пока используем только OI surge и whale % (из kwargs, появятся позже)

        # ── Композитный score ──
        # retail (0) ← balanced (50) → institutional (100)
        institutional_signals = []
        retail_signals = []

        # Liq: доминирование ликвидаций → розница
        if retail_liq_score > 30:
            retail_signals.append(retail_liq_score)

        # Объём: экстремально высокий → институциональный
        if volume_24h and volume_24h > 100_000_000:  # > $100M volume
            institutional_signals.append(60)
        elif volume_24h and volume_24h > 10_000_000:
            institutional_signals.append(40)

        # OI surge → institutional
        if oi_change_pct > OI_SURGE_PCT:
            institutional_signals.append(min(100, oi_change_pct * 10))

        # Whale volume high → institutional
        if whale_volume_pct > WHALE_VOLUME_HIGH:
            institutional_signals.append(80)
        elif whale_volume_pct > WHALE_VOLUME_LOW:
            institutional_signals.append(50)

        # Считаем общий score
        inst_avg = sum(institutional_signals) / len(institutional_signals) if institutional_signals else 0
        retail_avg = sum(retail_signals) / len(retail_signals) if retail_signals else 0

        if inst_avg > 50 and retail_avg < 20:
            score = 60 + inst_avg * 0.3
            label = "institutional"
        elif retail_avg > 40:
            score = max(0, 40 - retail_avg * 0.5)
            label = "retail"
        else:
            score = 40 + inst_avg * 0.3
            label = "balanced"

        score = max(0, min(100, score))

        meta = {
            "liq_volume_5m": liq_vol_5m,
            "liq_side_ratio": liq_side_ratio,
            "liq_count_5m": liq_count_5m,
            "institutional_signals": institutional_signals,
            "retail_signals": retail_signals,
        }

        return ParticipationDimension(
            label=label,
            score=round(score, 1),
            meta=meta,
        )
