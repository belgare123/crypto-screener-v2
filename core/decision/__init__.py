"""Decision Layer — адаптивное принятие решений между ConsensusEngine и SignalEngine.

Слои:
  models.Decision     — dataclass с action, confidence, sl, tp, reason, meta
  engine.DecisionEngine — adaptive thresholds + dynamic SL/TP

Поток:
  ConsensusResult → DecisionEngine.evaluate() → Decision → SignalEngine
"""
from __future__ import annotations

from core.decision.models import Decision
from core.decision.engine import DecisionEngine, get_decision_engine, reset_decision_engine

__all__ = [
    "Decision",
    "DecisionEngine",
    "get_decision_engine",
    "reset_decision_engine",
]
