"""
Strategy Base — BaseStrategy, StrategyMeta, StrategyResult, StrategyContext.
Level 4 в архитектуре ARCHITECTURE_V2.md.

Стратегия — это замена группе V1 сигналов.
Одна стратегия = несколько факторов из FeatureStore → 1 сигнал.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from context import MarketContext


# ──────────────────────────────────────────────
#  StrategyMeta — метаданные стратегии
# ──────────────────────────────────────────────


@dataclass
class StrategyMeta:
    name: str
    description: str
    category: str               # momentum, volume, whale, reversal, ...
    min_score: float = 40.0      # порог срабатывания (0-100)
    cooldown: int = 180          # антиспам, секунд
    enabled: bool = True
    timeframes: list[str] | None = None


# ──────────────────────────────────────────────
#  StrategyResult — результат проверки стратегии
# ──────────────────────────────────────────────


@dataclass
class StrategyResult:
    """Результат оценки стратегии — аналог SignalResult для L4."""
    strategy_name: str
    symbol: str
    direction: str               # buy / sell / neutral
    score: float                 # 0-100
    confidence: str              # LOW / MEDIUM / HIGH
    factors: list[dict]          # [{"name": "momentum", "score": 30, "direction": "buy", "detail": "..."}]
    context: MarketContext       # срез контекста на момент проверки
    meta: dict = field(default_factory=dict)   # дополнительные детали
    ts: float = field(default_factory=time.time)


# ──────────────────────────────────────────────
#  StrategyContext — всё, что нужно стратегии для работы
# ──────────────────────────────────────────────


@dataclass
class StrategyContext:
    """Контекст для стратегии — минимальный набор для evaluate()."""
    symbol: str
    features: Any | None = None      # FeatureEngine instance
    context: MarketContext | None = None  # ContextEngine срез
    exchange: str = "bybit"


# ──────────────────────────────────────────────
#  BaseStrategy
# ──────────────────────────────────────────────


class BaseStrategy(ABC):
    """Базовый класс стратегии. Все стратегии наследуются от него."""

    meta: ClassVar[StrategyMeta] = StrategyMeta("base", "Base strategy", "general")

    def __init__(self):
        self._last_state: dict[str, dict] = {}  # symbol → {"ts": float, "score": float, "direction": str}

    @abstractmethod
    async def evaluate(self, ctx: StrategyContext) -> StrategyResult | None:
        """
        Оценить стратегию.

        Args:
            ctx: контекст с symbol + features + context

        Returns:
            StrategyResult если сигнал есть, None если нет
        """
        ...

    def _result(
        self,
        ctx: StrategyContext,
        score: float,
        direction: str,
        factors: list[dict],
        confidence: str = "MEDIUM",
        meta: dict | None = None,
    ) -> StrategyResult:
        return StrategyResult(
            strategy_name=self.meta.name,
            symbol=ctx.symbol,
            direction=direction,
            score=min(100.0, max(0.0, score)),
            confidence=confidence,
            factors=factors,
            context=ctx.context or MarketContext.default(ctx.symbol),
            meta=meta or {},
            ts=time.time(),
        )

    @staticmethod
    def _classify_confidence(score: float) -> str:
        if score >= 80:
            return "HIGH"
        elif score >= 50:
            return "MEDIUM"
        return "LOW"

    # ── Cooldown (per-symbol) ──

    def can_send(self, symbol: str, score: float, direction: str) -> bool:
        """Проверить cooldown + degrade mode (per-symbol)."""
        now = time.time()
        cd = self.meta.cooldown

        # Получаем состояние для символа
        state = self._last_state.get(symbol)
        if state is None:
            # Первый раз — всегда можно
            self._last_state[symbol] = {"ts": now, "score": score, "direction": direction}
            return True

        # Cooldown ещё не прошёл
        if now - state["ts"] < cd:
            return False

        # Degrade mode: не шлём, если score не вырос
        if score <= state["score"] and direction == state["direction"]:
            return False

        # Обновляем состояние
        self._last_state[symbol] = {"ts": now, "score": score, "direction": direction}
        return True
