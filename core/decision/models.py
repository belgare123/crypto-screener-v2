"""Decision Engine — модели данных."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Decision:
    """Решение по символу — обогащённый ConsensusResult с адаптивными порогами и SL/TP.

    Поток:
      ConsensusResult → DecisionEngine → Decision → SignalEngine

    Поля:
      symbol    — тикер
      action    — "buy" / "sell" / "none"
      confidence — 0.0–1.0 (из ConsensusResult)
      score     — 0–100 (агрегированный score)
      sl        — динамический стоп-лосс (цена), None если не применим
      tp        — динамический тейк-профит (цена), None если не применим
      reason    — человеко-читаемая причина решения
      threshold — какой адаптивный порог был применён
      meta      — дополнительные данные (контекст, голоса и т.п.)
    """

    symbol: str
    action: str = "none"  # buy / sell / none
    confidence: float = 0.0
    score: float = 0.0
    sl: float | None = None
    tp: float | None = None
    reason: str = ""
    threshold: float = 60.0
    meta: dict[str, Any] = field(default_factory=dict)

    # ── Свойства ──

    @property
    def is_actionable(self) -> bool:
        """Есть ли торговый сигнал (buy или sell)."""
        return self.action in ("buy", "sell")

    @property
    def direction(self) -> str:
        """Алиас для action."""
        return self.action

    @property
    def short_reason(self) -> str:
        """Короткое описание причины."""
        if not self.is_actionable:
            return self.reason or "no_signal"
        parts = [self.reason]
        if self.sl is not None:
            parts.append(f"SL={self.sl}")
        if self.tp is not None:
            parts.append(f"TP={self.tp}")
        return " | ".join(parts)

    # ── Сериализация ──

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "confidence": round(self.confidence, 4),
            "score": round(self.score, 1),
            "sl": self.sl,
            "tp": self.tp,
            "reason": self.reason,
            "threshold": self.threshold,
            "actionable": self.is_actionable,
            "meta": self.meta,
        }
