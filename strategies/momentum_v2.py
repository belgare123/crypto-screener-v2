"""
Momentum Strategy (L4) — комбинация momentum + consecutive + context.
Level 4 в архитектуре ARCHITECTURE_V2.md.

Заменяет V1 сигналы:
- momentum.py (MomentumSignal)
- candle_technicals.py (ConsecutiveSignal)

Факторы:
1. Momentum — % change последней закрытой свечи (адаптивный порог от волатильности)
2. Consecutive — сколько свечей подряд одного цвета (c учётом контекста)
3. Context bonus — сессия + волатильность + тренд влияют на итоговый score

Dual-Run: V1 сигналы momentum + consecutive продолжают работать параллельно.
"""

from __future__ import annotations

import logging
from typing import Any

from strategies.base import BaseStrategy, StrategyContext, StrategyResult
from strategies import register_strategy

logger = logging.getLogger(__name__)


@register_strategy(
    name="momentum_v2",
    description="Комбинированный: momentum + consecutive + контекст рынка (тренд, волатильность, сессия)",
    category="momentum",
    min_score=35,
    cooldown=180,       # 3 мин — быстрее V1 (5 мин)
    timeframes=["1m"],
)
class MomentumStrategy(BaseStrategy):
    """
    Momentum Strategy — импульс + последовательные свечи + контекст.
    """

    # ── Параметры по умолчанию (адаптируются контекстом) ──
    BASE_MIN_MOVE_PCT = 0.3       # базовый порог движения (0.3%)
    CONSECUTIVE_MIN = 3            # минимальное количество свечей для consecutive
    MOMENTUM_MAX_SCORE = 40.0      # макс. баллов за momentum
    CONSECUTIVE_MAX_SCORE = 40.0   # макс. баллов за consecutive
    CONTEXT_MAX_SCORE = 20.0       # макс. баллов за контекст

    async def evaluate(self, ctx: StrategyContext) -> StrategyResult | None:
        """Оценить стратегию Momentum для символа."""
        symbol = ctx.symbol
        features = ctx.features
        context = ctx.context

        if features is None:
            logger.debug("[momentum_v2] skip %s: no features", symbol)
            return None

        # ── Фактор 1: Momentum ──
        momentum_factor = None
        try:
            momentum_factor = await self._factor_momentum(symbol, features, context)
        except Exception:
            logger.debug("[momentum_v2] momentum factor error %s", symbol, exc_info=True)

        # ── Фактор 2: Consecutive ──
        consecutive_factor = None
        try:
            consecutive_factor = await self._factor_consecutive(symbol, features, context)
        except Exception:
            logger.debug("[momentum_v2] consecutive factor error %s", symbol, exc_info=True)

        # ── Фактор 3: Context bonus ──
        context_bonus = self._factor_context(context)

        logger.info(
            "[momentum_v2] eval %s: momentum=%s consecutive=%s context=%.0f ctx_trend=%s ctx_vol=%s session=%s",
            symbol,
            f"{momentum_factor['score']}/{momentum_factor['direction']}" if momentum_factor else "None",
            f"{consecutive_factor['score']}/{consecutive_factor['direction']}" if consecutive_factor else "None",
            context_bonus["score"],
            context.trend if context else "?",
            context.volatility if context else "?",
            context.session_name if context else "?",
        )

        # Собираем факторы (только не-None)
        factors_raw = [momentum_factor, consecutive_factor, context_bonus]
        factors = [f for f in factors_raw if f is not None]

        if not factors:
            return None

        # ── Итоговый score ──
        total_score = sum(f["score"] for f in factors)
        total_score = min(100.0, total_score)

        if total_score < self.meta.min_score:
            return None

        # ── Направление: weighted vote ──
        buy_score = sum(f["score"] for f in factors if f.get("direction") == "buy")
        sell_score = sum(f["score"] for f in factors if f.get("direction") == "sell")

        if buy_score == sell_score:
            return None  # нейтрально

        direction = "buy" if buy_score > sell_score else "sell"
        confidence = self._classify_confidence(total_score)

        # ── Мета ──
        meta = {}
        for f in factors:
            meta[f["name"]] = f.get("detail", "")

        logger.debug(
            "[momentum_v2] %s score=%.0f dir=%s factors=%s confidence=%s ctx=%s trend=%s",
            symbol, total_score, direction,
            {f["name"]: f["score"] for f in factors},
            confidence,
            context.session_name if context else "?",
            context.trend if context else "?",
        )

        return self._result(
            ctx=ctx,
            score=total_score,
            direction=direction,
            factors=factors,
            confidence=confidence,
            meta=meta,
        )

    # ── Фактор 1: Momentum ──

    async def _factor_momentum(
        self,
        symbol: str,
        features: Any,
        context: Any,
    ) -> dict | None:
        """% change последней закрытой 1m свечи."""
        candles = await features.get_feature(symbol, "ohlcv.1m.buffer")
        if not candles or len(candles) < 3:
            return None

        # Берём 2 последние закрытые свечи (последняя в буфере — закрытая)
        last = candles[-1]
        prev = candles[-2]

        close_last = float(last["close"])
        close_prev = float(prev["close"])

        if close_prev == 0:
            return None

        change_pct = (close_last - close_prev) / close_prev * 100

        # Адаптивный порог: база * множитель сессии
        threshold = self._min_move_pct(context)
        threshold *= self._volatility_multiplier(context)

        if abs(change_pct) < threshold:
            return None

        # Score: линейно от порога до 0.5% → 40 баллов
        raw_score = abs(change_pct) / 0.5 * self.MOMENTUM_MAX_SCORE
        score = min(self.MOMENTUM_MAX_SCORE, raw_score)

        # Усиление контекстом: в тренде +25% к score
        if context and context.trend == "bull" and change_pct > 0:
            score *= 1.25
        elif context and context.trend == "bear" and change_pct < 0:
            score *= 1.25

        direction = "buy" if change_pct > 0 else "sell"
        detail = f"{change_pct:+.2f}% (threshold={threshold:.2f}%)"

        return {
            "name": "momentum",
            "score": round(score, 1),
            "direction": direction,
            "detail": detail,
        }

    # ── Фактор 2: Consecutive ──

    async def _factor_consecutive(
        self,
        symbol: str,
        features: Any,
        context: Any,
    ) -> dict | None:
        """Сколько свечей подряд одного цвета."""
        candles = await features.get_feature(symbol, "ohlcv.1m.buffer")
        if not candles or len(candles) < 5:
            logger.warning(
                "[momentum_v2] _factor_consecutive %s: buffer %s (len=%s)",
                symbol,
                "None" if candles is None else "empty" if not candles else f"too_short({len(candles)})",
                len(candles) if candles else 0,
            )
            return None

        # Определяем направление последней свечи
        last_close = float(candles[-1]["close"])
        last_open = float(candles[-1]["open"])
        last_dir = "buy" if last_close > last_open else "sell" if last_close < last_open else "flat"

        if last_dir == "flat":
            return None

        # Считаем сколько свечей подряд того же направления
        count = 1
        for i in range(len(candles) - 2, -1, -1):
            c = candles[i]
            c_close = float(c["close"])
            c_open = float(c["open"])
            c_dir = "buy" if c_close > c_open else "sell" if c_close < c_open else "flat"
            if c_dir == last_dir:
                count += 1
            else:
                break

        # Минимальный порог
        min_count = self.CONSECUTIVE_MIN
        # В тренде — снижаем порог
        if context and context.trend == last_dir:
            min_count = max(2, self.CONSECUTIVE_MIN - 1)

        if count < min_count:
            return None

        # Score: каждая свеча после 2-й даёт +10 баллов, адаптивно
        context_mult = 1.0
        if context:
            # В сессии с высоким momentum_weight — consecutive весомее
            context_mult = context.session_momentum_weight

        raw_score = (count - 2) * 10 * context_mult
        score = min(self.CONSECUTIVE_MAX_SCORE, raw_score)

        detail = f"{count} ({last_dir})" if context is None else f"{count} ({last_dir}) in {context.session_name}"

        return {
            "name": "consecutive",
            "score": round(score, 1),
            "direction": last_dir,
            "detail": detail,
        }

    # ── Фактор 3: Context bonus ──

    def _factor_context(self, context: Any) -> dict:
        """Контекстный бонус: сессия + волатильность."""
        if context is None:
            return {"name": "context", "score": 0.0, "direction": "neutral", "detail": "no context"}

        bonus = 0.0
        detail_parts = []

        # 1) Volatility expansion — импульсы сильнее
        if context.volatility_state == "expansion":
            bonus += 5.0
            detail_parts.append("vol expansion")

        # 2) Сессия с высоким momentum_weight
        mw = context.session_momentum_weight
        if mw > 1.0:
            bonus += 5.0
            detail_parts.append(f"session ({context.session_name} mw={mw})")

        # 3) Режим extreme — добавляем вес
        if context.regime_score >= 80:
            bonus += 5.0
            detail_parts.append("extreme regime")

        bonus = min(self.CONTEXT_MAX_SCORE, bonus)

        return {
            "name": "context",
            "score": round(bonus, 1),
            "direction": "neutral",
            "detail": ", ".join(detail_parts) if detail_parts else "neutral",
        }

    # ── Вспомогательное ──

    def _min_move_pct(self, context: Any) -> float:
        """Адаптивный порог движения от сессии."""
        if context is None:
            return self.BASE_MIN_MOVE_PCT
        return self.BASE_MIN_MOVE_PCT * context.threshold_multiplier

    def _volatility_multiplier(self, context: Any) -> float:
        """Множитель от волатильности: выше волатильность → выше порог."""
        if context is None:
            return 1.0
        multipliers = {
            "low": 0.8,
            "normal": 1.0,
            "high": 1.3,
            "extreme": 1.6,
        }
        return multipliers.get(context.volatility, 1.0)
