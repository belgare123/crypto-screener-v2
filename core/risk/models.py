"""
Risk Engine — модели.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RiskVerdict(Enum):
    ALLOW = "allow"
    BLOCK = "block"
    REDUCE = "reduce"  # сигнал проходит, но с уменьшенным размером / пониженным confidence


@dataclass
class RiskReason:
    """Причина блокировки/редукции."""
    rule_name: str
    verdict: RiskVerdict
    message: str
    severity: float = 0.0  # 0-1, насколько критично
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RiskResult:
    """Результат полной проверки RiskEngine."""
    symbol: str
    verdict: RiskVerdict = RiskVerdict.ALLOW
    reasons: list[RiskReason] = field(default_factory=list)
    confidence_multiplier: float = 1.0  # 0.0-1.0, REDUCE уменьшает confidence

    def add(self, reason: RiskReason):
        self.reasons.append(reason)
        if reason.verdict == RiskVerdict.BLOCK:
            self.verdict = RiskVerdict.BLOCK
        elif reason.verdict == RiskVerdict.REDUCE and self.verdict == RiskVerdict.ALLOW:
            self.verdict = RiskVerdict.REDUCE
            self.confidence_multiplier = min(self.confidence_multiplier, 1.0 - reason.severity * 0.5)

    @property
    def blocked(self) -> bool:
        return self.verdict == RiskVerdict.BLOCK

    @property
    def allowed(self) -> bool:
        return self.verdict == RiskVerdict.ALLOW

    @property
    def summary(self) -> str:
        if self.blocked:
            reasons = "; ".join(r.message for r in self.reasons if r.verdict == RiskVerdict.BLOCK)
            return f"BLOCKED: {reasons}"
        if self.verdict == RiskVerdict.REDUCE:
            return f"REDUCED (x{self.confidence_multiplier:.2f})"
        return "ALLOW"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "verdict": self.verdict.value,
            "confidence_multiplier": self.confidence_multiplier,
            "reasons": [
                {"rule": r.rule_name, "verdict": r.verdict.value, "msg": r.message}
                for r in self.reasons
            ],
        }


@dataclass
class RiskContext:
    """Контекст, который RiskEngine передаёт каждому правилу."""
    symbol: str
    exchange: str

    # Рыночные данные
    spread_bps: float = 0.0       # текущий спред в bps
    atr_14_pct: float = 0.0       # ATR(14) в % от цены
    volume_24h_usdt: float = 0.0
    oi_usdt: float = 0.0
    volatility_1h: float = 0.0     # STD в % за час
    price: float = 0.0

    # Данные сигнала
    signal_name: str = ""
    signal_score: float = 0.0
    signal_direction: str = ""     # buy / sell

    # Стейт
    current_hour: int = 0          # 0-23 UTC
    ts: float = 0.0
