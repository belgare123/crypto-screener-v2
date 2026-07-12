"""
ConsensusEngine — взвешенное голосование стратегий.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from core.consensus.models import (
    SignalDirection, SignalVote, ConsensusResult,
)

logger = logging.getLogger(__name__)


class ConsensusEngine:
    """Агрегация голосов стратегий в консенсусное решение.

    Механика:
    - Каждый SignalVote имеет weight (от historical_winrate, default 1.0)
    - Взвешенный подсчёт buy/sell голосов
    - Confidence = max(buy_weighted, sell_weighted) / total_weighted
    - Score = (buy_weighted - sell_weighted) / total_weighted * 100
    - regime_multiplier корректирует confidence по MarketState
    """

    def __init__(self, shadow: bool = True):
        self.shadow = shadow
        self._winrates: dict[str, float] = {}  # strategy_name → winrate (0–1)

    def set_winrate(self, strategy_name: str, winrate: float):
        """Установить historical winrate для стратегии."""
        self._winrates[strategy_name] = max(0.0, min(1.0, winrate))

    def set_winrates(self, winrates: dict[str, float]):
        for name, wr in winrates.items():
            self.set_winrate(name, wr)

    def get_weight(self, strategy_name: str) -> float:
        """Вес стратегии: winrate (минимум 0.3)."""
        base = self._winrates.get(strategy_name, 1.0)
        if base < 0.2:
            return 0.2  # минимальный вес всегда есть
        return base

    def evaluate(self, votes: list[SignalVote], regime_multiplier: float = 1.0) -> ConsensusResult:
        """Превратить список голосов в ConsensusResult."""
        if not votes:
            return ConsensusResult(symbol="")

        symbol = votes[0].strategy_name if len(votes) == 1 else "aggregated"
        # Try to get symbol from vote extra if available, or from strategy name convention
        for v in votes:
            sym = v.extra.get("symbol", "")
            if sym:
                symbol = sym
                break

        # Но лучше пусть вызывающий код ставит symbol. Используем первый попавшийся.
        # На самом деле ConsensusResult.symbol должен ставиться извне.
        # Определяем символ через extra каждого vote
        symbols = {v.extra.get("symbol", "") for v in votes if v.extra.get("symbol")}
        if len(symbols) == 1:
            symbol = symbols.pop()
        elif len(symbols) > 1:
            symbol = "mixed"

        total_weight = 0.0
        buy_weight = 0.0
        sell_weight = 0.0
        buy_votes_count = 0
        sell_votes_count = 0

        weighted_votes: list[SignalVote] = []
        for v in votes:
            # Применяем вес от winrate
            w = v.weight * self.get_weight(v.strategy_name)
            weighted = SignalVote(
                strategy_name=v.strategy_name,
                direction=v.direction,
                confidence=v.confidence,
                score=v.score,
                weight=round(w, 4),
                reason=v.reason,
                extra=v.extra,
            )
            weighted_votes.append(weighted)

            total_weight += w
            if v.direction == SignalDirection.BUY:
                buy_weight += w * v.confidence
                buy_votes_count += 1
            elif v.direction == SignalDirection.SELL:
                sell_weight += w * v.confidence
                sell_votes_count += 1
            # NEUTRAL = zero weight

        if total_weight == 0:
            return ConsensusResult(symbol=symbol)

        # Направление: больше взвешенный объём
        if buy_weight > sell_weight:
            direction = SignalDirection.BUY
            confidence = buy_weight / total_weight
            score = ((buy_weight - sell_weight) / total_weight) * 100
        elif sell_weight > buy_weight:
            direction = SignalDirection.SELL
            confidence = sell_weight / total_weight
            score = ((sell_weight - buy_weight) / total_weight) * 100
        else:
            direction = SignalDirection.NEUTRAL
            confidence = 0.0
            score = 0.0

        # Умножаем confidence на regime_multiplier
        final_confidence = min(confidence * regime_multiplier, 1.0)

        result = ConsensusResult(
            symbol=symbol,
            direction=direction,
            score=round(min(score, 100), 1),
            confidence=round(final_confidence, 4),
            buy_votes=buy_votes_count,
            sell_votes=sell_votes_count,
            total_votes=len(votes),
            votes=weighted_votes,
            regime_multiplier=regime_multiplier,
        )

        if self.shadow:
            logger.debug(
                "[consensus:shadow] %s → %s score=%.1f conf=%.3f B=%d/S=%d",
                symbol, direction.value, result.score, result.confidence,
                buy_votes_count, sell_votes_count,
            )

        return result


# Singleton
_consensus_engine: ConsensusEngine | None = None


def get_consensus_engine(shadow: bool = True) -> ConsensusEngine:
    global _consensus_engine
    if _consensus_engine is None:
        _consensus_engine = ConsensusEngine(shadow=shadow)
    return _consensus_engine


def reset_consensus_engine():
    global _consensus_engine
    _consensus_engine = None
