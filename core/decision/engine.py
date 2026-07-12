"""
DecisionEngine — адаптивный слой принятия решений между ConsensusEngine и SignalEngine.

Что добавляет:
1. Адаптивные пороги уверенности — зависят от тренда, волатильности, режима
2. Динамические SL/TP — на основе ATR и волатильности
3. Обоснование решения — какие факторы повлияли
4. Event-driven пересчёт через подписку на ContextEngine (опционально)

Поток:
  ConsensusResult → DecisionEngine.evaluate() → Decision → SignalEngine
"""

from __future__ import annotations

import logging
from typing import Any

from core.consensus.models import ConsensusResult, SignalDirection
from core.features.store import FeatureStore, get_feature_store

# Чтобы избежать циклических импортов на этапе сборки
import context.market_context as mctx

logger = logging.getLogger(__name__)


class DecisionEngine:
    """Адаптивный слой принятия решений.

    Usage:
        engine = DecisionEngine(context_engine, consensus_engine, feature_store)
        decision = await engine.evaluate("BTC/USDT:USDT", consensus)
    """

    # Пороги по умолчанию для разных режимов (проценты 0–100)
    DEFAULT_THRESHOLD = 60.0

    # Модификаторы порогов
    MODIFIERS: dict[str, float] = {
        "flat": 10.0,         # во флэте — выше уверенность
        "high": 5.0,          # высокая вола — осторожнее
        "extreme": 8.0,       # экстремальная вола — ещё осторожнее
        "expansion": 5.0,     # расширение — меньше доверия
    }

    # Множители SL/TP
    SL_ATR_MULTIPLIER = 1.5     # SL = price - 1.5 * ATR
    TP_ATR_MULTIPLIER = 3.0     # TP = price + 3.0 * ATR

    def __init__(
        self,
        context_engine=None,
        consensus_engine=None,
        feature_store: FeatureStore | None = None,
        default_threshold: float = 60.0,
    ):
        from context import ContextEngine, get_context_engine

        self._ce = context_engine or get_context_engine()
        self._consensus = consensus_engine
        self._fs = feature_store or get_feature_store()
        self._default_threshold = default_threshold

        # Shadow mode — логируем, но не блокируем
        self.shadow = True

    # ── Core ──

    async def evaluate(
        self,
        symbol: str,
        consensus: ConsensusResult,
    ) -> "Decision":
        """Принять решение по символу на основе консенсуса и контекста.

        Args:
            symbol: тикер
            consensus: результат голосования ConsensusEngine

        Returns:
            Decision с action=buy/sell/none, адаптивными порогами, SL/TP.
        """
        from core.decision.models import Decision

        # 1. Контекст (кэшированный)
        ctx = await self._ce.get_context(symbol)

        # 2. Адаптивный порог
        threshold = self._calc_threshold(ctx)

        # 3. Проверка — достоин ли сигнал прохода
        if not self._is_qualified(consensus, threshold):
            reason = self._build_reject_reason(consensus, threshold)
            decision = Decision(
                symbol=symbol,
                action="none",
                confidence=consensus.confidence,
                score=consensus.score,
                reason=reason,
                threshold=threshold,
                meta={
                    "context": ctx.to_dict(),
                    "votes": len(consensus.votes),
                },
            )
            if self.shadow:
                logger.debug(
                    "[decision:shadow] %s REJECT %s (need >= %.0f%%)",
                    symbol, reason, threshold,
                )
            return decision

        # 4. Динамические SL/TP
        sl, tp = await self._calc_sltp(symbol, consensus)

        # 5. Решение
        decision = Decision(
            symbol=symbol,
            action=consensus.direction.value,
            confidence=consensus.confidence,
            score=consensus.score,
            sl=sl,
            tp=tp,
            reason=f"pass (threshold adj={threshold:.0f}%)",
            threshold=threshold,
            meta={
                "context": ctx.to_dict(),
                "votes": len(consensus.votes),
                "atr_sl_tp": {"sl": sl, "tp": tp},
            },
        )

        if self.shadow:
            logger.info(
                "[decision:shadow] %s → %s (conf=%.2f score=%.1f "
                "threshold=%.0f%%) SL=%.4f TP=%.4f",
                symbol, consensus.direction.value,
                consensus.confidence, consensus.score,
                threshold, sl or 0, tp or 0,
            )

        return decision

    # ── Адаптивные пороги ──

    def _calc_threshold(self, ctx: mctx.MarketContext) -> float:
        """Рассчитать порог уверенности на основе состояния рынка.

        База 60% + модификаторы от тренда и волатильности.
        """
        t = self._default_threshold

        if ctx.trend == "flat":
            t += self.MODIFIERS["flat"]
        elif ctx.trend == "bull":
            t -= 3.0  # тренд работает в нашу пользу — чуть ниже порог

        if ctx.volatility in ("high", "extreme"):
            t += self.MODIFIERS.get(ctx.volatility, 0)

        if ctx.volatility_state == "expansion":
            t += self.MODIFIERS["expansion"]

        return min(t, 95.0)  # не выше 95%

    def _is_qualified(self, consensus: ConsensusResult, threshold: float) -> bool:
        """Проверяет, проходит ли ConsensusResult через адаптивный порог."""
        if consensus.direction == SignalDirection.NEUTRAL:
            return False
        return consensus.confidence * 100 >= threshold

    def _build_reject_reason(self, consensus: ConsensusResult, threshold: float) -> str:
        """Формирует человеко-читаемую причину отказа."""
        if consensus.direction == SignalDirection.NEUTRAL:
            return "neutral_direction"
        conf_pct = consensus.confidence * 100
        if conf_pct < threshold:
            return f"low_confidence ({conf_pct:.1f}% < {threshold:.0f}%)"
        return "unknown_reject"

    # ── Динамические SL/TP ──

    async def _calc_sltp(
        self,
        symbol: str,
        consensus: ConsensusResult,
    ) -> tuple[float | None, float | None]:
        """Рассчитать стоп-лосс и тейк-профит на основе ATR.

        SL = price - direction * 1.5 * ATR_value
        TP = price + direction * 3.0 * ATR_value
        """
        try:
            # Цена и ATR из FeatureStore
            price = await self._fs.get(symbol, "ticker.last")
            atr_pct = await self._fs.get(symbol, "vol.atr_pct")
        except Exception:
            logger.debug("[decision] no price/atr for %s", symbol, exc_info=True)
            return None, None

        if not price or not atr_pct or atr_pct <= 0:
            return None, None

        atr_value = price * (atr_pct / 100.0)
        direction = 1 if consensus.direction == SignalDirection.BUY else -1

        sl = price - direction * self.SL_ATR_MULTIPLIER * atr_value
        tp = price + direction * self.TP_ATR_MULTIPLIER * atr_value

        return round(sl, 8), round(tp, 8)

    # ── Batch ──

    async def evaluate_batch(
        self,
        symbols_consensus: dict[str, ConsensusResult],
    ) -> dict[str, "Decision"]:
        """Оценить несколько символов (один тик).

        Returns:
            dict: symbol → Decision
        """
        from core.decision.models import Decision

        results: dict[str, Decision] = {}
        for symbol, consensus in symbols_consensus.items():
            try:
                results[symbol] = await self.evaluate(symbol, consensus)
            except Exception:
                logger.exception("[decision] evaluate %s failed", symbol)
                results[symbol] = Decision(
                    symbol=symbol,
                    action="none",
                    reason="evaluation_error",
                )
        return results


# ── Singleton ──

_decision_engine: DecisionEngine | None = None


def get_decision_engine() -> DecisionEngine:
    global _decision_engine
    if _decision_engine is None:
        _decision_engine = DecisionEngine()
    return _decision_engine


def reset_decision_engine():
    global _decision_engine
    _decision_engine = None
