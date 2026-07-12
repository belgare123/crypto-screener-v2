"""
Consensus Engine — модели данных.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SignalDirection(Enum):
    BUY = "buy"
    SELL = "sell"
    NEUTRAL = "neutral"


@dataclass
class SignalVote:
    """Голос одной стратегии."""
    strategy_name: str
    direction: SignalDirection
    confidence: float  # 0.0–1.0
    score: float = 0.0  # сырой score сигнала (опционально)
    weight: float = 1.0  # вес стратегии (из historical_winrate)
    reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConsensusResult:
    """Результат консенсуса по одному символу."""
    symbol: str
    direction: SignalDirection = SignalDirection.NEUTRAL
    score: float = 0.0          # агрегированный score 0–100
    confidence: float = 0.0     # 0.0–1.0
    buy_votes: int = 0
    sell_votes: int = 0
    total_votes: int = 0
    votes: list[SignalVote] = field(default_factory=list)
    regime_multiplier: float = 1.0  # корректировка от StateEngine

    @property
    def buy_ratio(self) -> float:
        return self.buy_votes / self.total_votes if self.total_votes > 0 else 0.0

    @property
    def meaningful(self) -> bool:
        return self.total_votes >= 2 and self.confidence > 0.3

    @property
    def net_score(self) -> float:
        """Итоговый знак направления: +score для BUY, -score для SELL."""
        if self.direction == SignalDirection.BUY:
            return self.score
        elif self.direction == SignalDirection.SELL:
            return -self.score
        return 0.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction.value,
            "score": round(self.score, 1),
            "confidence": round(self.confidence, 3),
            "buy_votes": self.buy_votes,
            "sell_votes": self.sell_votes,
            "total_votes": self.total_votes,
            "meaningful": self.meaningful,
            "votes": [
                {"strategy": v.strategy_name, "direction": v.direction.value,
                 "confidence": v.confidence, "weight": v.weight}
                for v in self.votes
            ],
        }

    def short_str(self) -> str:
        if not self.meaningful:
            return f"{self.symbol}: skippable ({self.total_votes} votes, conf={self.confidence:.2f})"
        return (f"{self.symbol}: {self.direction.value.upper()} "
                f"score={self.score:.0f} conf={self.confidence:.2f} "
                f"B:{self.buy_votes}/S:{self.sell_votes}")


@dataclass
class OpportunityWindow:
    """Накопление сигналов за временное окно для ранжирования."""
    symbol: str
    entries: list[ConsensusResult] = field(default_factory=list)
    first_seen: float = 0.0
    last_seen: float = 0.0
    best_score: float = 0.0
    total_signals: int = 0
    expired: bool = False
