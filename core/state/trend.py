"""
TrendDetector — определяет направление и силу тренда.

Источник: FeatureStore (IndicatorsFeatureCalculator + VolatilityFeatureCalculator).
- EMA alignment: цена vs EMA(8) vs EMA(21) vs EMA(50)
- ADX(14): сила тренда (0-100)
- MACD: импульс + гистограмма
- Slope: наклон цены за N свечей (из OHLCV)

Выход:
- label: strong_up / weak_up / sideways / weak_down / strong_down
- score: 0-100 (100 = сильнейший тренд)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from core.state.market_state import TrendDimension

logger = logging.getLogger(__name__)


# ── Пороги ──

ADX_STRONG = 25        # ADX >= 25 = сильный тренд
ADX_WEAK = 20          # ADX < 20 = слабый/боковой
EMA_ALIGN_STRONG = 3   # сколько EMA согласны для strong
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
MACD_MOMENTUM_LOOKBACK = 3  # свечей для проверки ускорения


@dataclass
class TrendDetectorConfig:
    """Конфигурация TrendDetector."""
    adx_strong: int = ADX_STRONG
    adx_weak: int = ADX_WEAK
    ema_align_strong: int = EMA_ALIGN_STRONG
    rsi_oversold: int = RSI_OVERSOLD
    rsi_overbought: int = RSI_OVERBOUGHT
    default_ttl: float = 30.0  # пересчёт раз в 30 сек


class TrendDetector:
    """
    Определяет состояние тренда через FeatureEngine.

    Usage:
        detector = TrendDetector()
        trend = await detector.detect("BTC/USDT:USDT")
    """

    def __init__(self, config: TrendDetectorConfig | None = None, feature_store=None):
        from core.features.store import get_feature_store
        self.config = config or TrendDetectorConfig()
        self._store = feature_store or get_feature_store()

    async def detect(self, symbol: str) -> TrendDimension:
        """
        Определить трендовое состояние для символа.

        Читает все необходимые фичи из FeatureStore параллельно.
        """
        names = [
            "ema.8", "ema.21", "ema.50", "ema.200",
            "rsi.14", "adx.14", "macd", "vol.atr_pct",
        ]
        features = await self._store.get_multi([symbol], names)
        feats = features.get(symbol, {})

        ema8 = feats.get("ema.8")
        ema21 = feats.get("ema.21")
        ema50 = feats.get("ema.50")
        ema200 = feats.get("ema.200")
        rsi = feats.get("rsi.14")
        adx = feats.get("adx.14")
        macd = feats.get("macd")

        # ── EMA alignment: сколько EMA «согласны» (бычье расположение) ──
        ema_bullish = 0
        ema_bearish = 0
        ema_values = []

        if ema8 is not None and ema21 is not None:
            if ema8 > ema21:
                ema_bullish += 1
            else:
                ema_bearish += 1
            ema_values.extend([ema8, ema21])

        if ema21 is not None and ema50 is not None:
            if ema21 > ema50:
                ema_bullish += 1
            else:
                ema_bearish += 1
            ema_values.append(ema50)

        if ema50 is not None and ema200 is not None:
            if ema50 > ema200:
                ema_bullish += 1
            else:
                ema_bearish += 1
            ema_values.append(ema200)

        # ── MACD ──
        macd_bullish = 0
        macd_bearish = 0
        macd_info = {}

        if isinstance(macd, dict):
            macd_line = macd.get("macd", 0)
            signal = macd.get("signal", 0)
            hist = macd.get("histogram", 0)
            macd_info = {"line": macd_line, "signal": signal, "hist": hist}

            if macd_line > signal:
                macd_bullish += 1
            else:
                macd_bearish += 1

            if hist > 0:
                macd_bullish += 0.5
            elif hist < 0:
                macd_bearish += 0.5

        # ── RSI ──
        rsi_bullish = 0
        rsi_bearish = 0
        if isinstance(rsi, (int, float)):
            if rsi > 50:
                rsi_bullish += 1
            else:
                rsi_bearish += 1
            if rsi > RSI_OVERBOUGHT:
                rsi_bearish += 0.5  # перекупленность — риск разворота
            elif rsi < RSI_OVERSOLD:
                rsi_bullish += 0.5  # перепроданность — риск разворота

        # ── ADX — сила тренда ──
        adx_strong = False
        if isinstance(adx, (int, float)):
            adx_strong = adx >= self.config.adx_strong

        # ── Итоговый подсчёт ──
        total_bullish = ema_bullish + macd_bullish + rsi_bullish
        total_bearish = ema_bearish + macd_bearish + rsi_bearish
        max_possible = 8  # 3 EMA + 1.5 MACD + 1.5 RSI + 2 ADX = 8

        # Направление
        if total_bullish > total_bearish and adx_strong:
            label = "strong_up"
        elif total_bullish > total_bearish:
            label = "weak_up"
        elif total_bearish > total_bullish and adx_strong:
            label = "strong_down"
        elif total_bearish > total_bullish:
            label = "weak_down"
        else:
            label = "sideways"

        # Score: 0-100, пропорционально консенсусу + ADX
        max_side = max(total_bullish, total_bearish)
        if max_possible > 0:
            consensus = max_side / max_possible
        else:
            consensus = 0.0

        score = consensus * 100.0
        if adx_strong:
            score = min(100, score * 1.2)  # ADX усиливает уверенность

        meta = {
            "ema_bullish": ema_bullish,
            "ema_bearish": ema_bearish,
            "macd_bullish": macd_bullish,
            "macd_bearish": macd_bearish,
            "rsi_value": rsi,
            "adx_value": adx,
            "total_bullish": total_bullish,
            "total_bearish": total_bearish,
        }

        logger.debug(
            "[trend] %s → %s score=%.0f ema=%d:%d macd=%.1f:%.1f rsi=%s adx=%s",
            symbol, label, score,
            ema_bullish, ema_bearish,
            macd_bullish, macd_bearish,
            f"{rsi:.1f}" if isinstance(rsi, float) else "N/A",
            f"{adx:.1f}" if isinstance(adx, float) else "N/A",
        )

        return TrendDimension(
            label=label,
            score=round(score, 1),
            ema8=ema8,
            ema21=ema21,
            ema50=ema50,
            ema200=ema200,
            adx=adx,
            macd=macd_info,
            meta=meta,
        )
