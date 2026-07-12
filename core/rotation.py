"""
Rotation Detector — отслеживание перетока капитала между секторами.

Строится поверх SectorScannerEngine.
Добавляет:
  - История лидеров секторов (последние N снэпшотов)
  - Обнаружение смены: L1→Meme, DeFi→L1, и т.д.
  - Оценка силы ротации (strong / moderate / weak)
  - Сигнал при значимой ротации

Обновление: раз в 60 секунд (через sector_engine.scan)
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from core import SignalResult
from core.sector_scanner import SectorScannerEngine, SECTOR_MAP

logger = logging.getLogger(__name__)

# Типы секторов для анализа направления перетока
SECTOR_TYPE = {
    "L1": "core",
    "Meme": "speculative",
    "DeFi": "defi",
    "Infra": "core",
    "Payment": "payment",
    "Ecosystem": "ecosystem",
}


@dataclass
class RotationSnapshot:
    timestamp: float = 0.0
    current_leader: str | None = None
    previous_leader: str | None = None
    leader_history: list[str | None] = field(default_factory=list)
    direction: str = "none"          # risk_on / risk_off / neutral
    strength: str = "none"           # strong / moderate / weak / none
    sector_rankings: list[tuple[str, float]] = field(default_factory=list)  # [(sector, avg_rs_5m)]
    rotation_text: str = ""


# Типы ротации
_ROTATION_PATTERNS = {
    # risk_on: капитал идёт в Meme / speculative из core
    "to_meme_from_core": {"from": "core", "to": "speculative", "direction": "risk_on", "strength": "strong"},
    "to_meme_from_defi": {"from": "defi", "to": "speculative", "direction": "risk_on", "strength": "moderate"},
    # risk_off: капитал идёт в core (L1) из speculative
    "to_core_from_meme": {"from": "speculative", "to": "core", "direction": "risk_off", "strength": "strong"},
    "to_core_from_defi": {"from": "defi", "to": "core", "direction": "risk_off", "strength": "moderate"},
    # нейтральное смещение: DeFi → L1
    "to_core_from_defi_mild": {"from": "defi", "to": "core", "direction": "risk_off", "strength": "weak"},
}


class RotationDetector:
    """
    Детектор ротации капитала между секторами.
    Делает snapshot поверх SectorScannerEngine и определяет направление перетока.
    """

    def __init__(self, sector_engine: SectorScannerEngine, history_size: int = 10):
        self._sector_engine = sector_engine
        self._history = deque(maxlen=history_size)
        self._last_snapshot: RotationSnapshot = RotationSnapshot()

    @property
    def last_snapshot(self) -> RotationSnapshot:
        return self._last_snapshot

    def tick(self, rankings: list[dict]) -> RotationSnapshot:
        """Обновить ротацию."""

        # Берём свежий снимок секторов
        sector_snap = self._sector_engine.scan(rankings)

        if not sector_snap.leading_sector or not sector_snap.sectors:
            return self._last_snapshot

        # Запоминаем лидера
        self._history.append(sector_snap.leading_sector)

        # Предыдущий лидер
        prev_leader = list(self._history)[-2] if len(self._history) >= 2 else None

        # Определяем направления
        direction, strength = self._classify_rotation(
            prev_leader, sector_snap.leading_sector
        )

        # Ранжинг секторов
        rankings_list = sorted(
            sector_snap.sectors.items(),
            key=lambda kv: kv[1]["avg_rs_5m"],
            reverse=True,
        )
        sector_rankings = [(s, d["avg_rs_5m"]) for s, d in rankings_list]

        # Текст ротации
        rot_text = self._format_rotation(
            prev_leader, sector_snap.leading_sector, direction, strength,
            sector_rankings
        )

        self._last_snapshot = RotationSnapshot(
            timestamp=time.time(),
            current_leader=sector_snap.leading_sector,
            previous_leader=prev_leader,
            leader_history=list(self._history),
            direction=direction,
            strength=strength,
            sector_rankings=sector_rankings,
            rotation_text=rot_text,
        )

        return self._last_snapshot

    def _classify_rotation(
        self,
        prev_leader: str | None,
        curr_leader: str | None,
    ) -> tuple[str, str]:
        """Определить направление и силу ротации."""
        if not prev_leader or not curr_leader or prev_leader == curr_leader:
            return "neutral", "none"

        prev_type = SECTOR_TYPE.get(prev_leader, "unknown")
        curr_type = SECTOR_TYPE.get(curr_leader, "unknown")

        # Переток из core в speculative (L1→Meme)
        if prev_type == "core" and curr_type == "speculative":
            return "risk_on", "strong"
        if prev_type == "defi" and curr_type == "speculative":
            return "risk_on", "moderate"

        # Переток в core (risk_off)
        if prev_type == "speculative" and curr_type == "core":
            return "risk_off", "strong"
        if prev_type == "defi" and curr_type == "core":
            return "risk_off", "moderate"

        # Другие смещения — слабая ротация
        if prev_type != curr_type:
            return "risk_on" if curr_type in ("speculative",) else "risk_off", "weak"

        return "neutral", "weak"

    def _format_rotation(
        self,
        prev: str | None,
        curr: str | None,
        direction: str,
        strength: str,
        rankings: list[tuple[str, float]],
    ) -> str:
        """Форматировать описание ротации."""
        if not prev or not curr:
            return "Нет данных"

        if prev == curr:
            return f"Лидер не изменился: {curr}"

        emoji = "🟢" if direction == "risk_on" else ("🔴" if direction == "risk_off" else "⚪")
        strength_label = {"strong": "сильная", "moderate": "умеренная", "weak": "слабая", "none": ""}.get(strength, "")

        parts = [
            f"{emoji} Ротация: {prev} → {curr}",
        ]
        if strength_label:
            parts.append(f"  Сила: {strength_label}")
        parts.append(f"  Направление: {'Risk ON (в риск)' if direction == 'risk_on' else 'Risk OFF (из риска)' if direction == 'risk_off' else 'нейтральное'}")

        # Ранжинг секторов
        if rankings:
            rank_lines = []
            for i, (s, rs) in enumerate(rankings, 1):
                em = "🟢" if rs > 0 else ("🔴" if rs < 0 else "⚪")
                rank_lines.append(f"  {i}. {s} ({em} {rs:+.2f})")
            parts.append("  📊 Ранжинг:\n" + "\n".join(rank_lines))

        return "\n".join(parts)

    def to_signal(self) -> SignalResult | None:
        """Создать SignalResult при сильной или умеренной ротации."""
        snap = self._last_snapshot
        if snap.strength in ("none", "weak"):
            return None

        score_map = {"strong": 75, "moderate": 60}
        score = score_map.get(snap.strength, 50)
        direction = "buy" if snap.direction == "risk_on" else "sell"

        return SignalResult(
            signal_name="rotation",
            symbol="MARKET",
            exchange="bybit",
            score=score,
            direction=direction,
            meta={
                "previous_leader": snap.previous_leader,
                "current_leader": snap.current_leader,
                "direction": snap.direction,
                "strength": snap.strength,
                "sector_rankings": snap.sector_rankings,
                "description": snap.rotation_text.split("\n")[0] if snap.rotation_text else "",
            },
            ts=snap.timestamp,
            cooldown=3600,
        )

    def get_info(self) -> dict[str, Any]:
        snap = self._last_snapshot
        return {
            "current_leader": snap.current_leader,
            "previous_leader": snap.previous_leader,
            "direction": snap.direction,
            "strength": snap.strength,
            "rankings": snap.sector_rankings,
            "history": snap.leader_history,
        }


_rotation_detector: RotationDetector | None = None


def get_rotation_detector(sector_engine: SectorScannerEngine | None = None) -> RotationDetector:
    global _rotation_detector
    if _rotation_detector is None and sector_engine is not None:
        _rotation_detector = RotationDetector(sector_engine)
    return _rotation_detector
