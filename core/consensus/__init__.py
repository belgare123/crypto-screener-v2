"""
Consensus Engine — агрегация сигналов в консенсусное решение.

Поток:
1. Strategy Engine собирает SignalVote[] от каждой стратегии
2. ConsensusEngine взвешивает по historical_winrate + state_regime
3. OpportunityRanking накапливает сигналы за N минут
4. Выдаёт ConsensusResult (direction, score, confidence, votes)
"""
from __future__ import annotations

from .models import SignalVote, ConsensusResult, OpportunityWindow, SignalDirection
from .engine import ConsensusEngine, get_consensus_engine, reset_consensus_engine
from .rank import OpportunityRanking

__all__ = [
    "SignalVote", "ConsensusResult", "OpportunityWindow", "SignalDirection",
    "ConsensusEngine", "get_consensus_engine", "reset_consensus_engine",
    "OpportunityRanking",
]
