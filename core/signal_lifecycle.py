"""
Signal Lifecycle + Confidence Drift — отслеживание стадий и падения уверенности.

Lifecycle:
  Born (0-2 мин) → Growing (score ↑) → Confirmed (score стабилен > 5 мин)
  → Weakening (score ↓) → Dead (> 30 мин без обновлений)

Confidence Drift:
  ⚠ Signal weakening detected:
    BTC whale @ 64200: 91 → 74 → 58 за 10 мин
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from core import SignalResult

logger = logging.getLogger(__name__)

DRIFT_THRESHOLD = 20          # % падения score для алерта
DRIFT_WINDOW_MINUTES = 15     # окно наблюдения
LIFECYCLE_DEAD_AFTER = 1800   # 30 минут без обновлений
LIFECYCLE_CONFIRMED_MIN = 300 # 5 минут стабильности


@dataclass
class SignalTrace:
    signal_name: str = ""
    symbol: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0
    current_stage: str = "born"       # born / growing / confirmed / weakening / dead
    score_history: list[tuple[float, float]] = field(default_factory=list)  # [(timestamp, score), ...]
    max_score: float = 0.0
    min_score: float = 0.0
    score_slope: float = 0.0          # + = rising, - = falling
    direction: str = "neutral"
    detection_count: int = 0
    drift_detected: bool = False


@dataclass
class LifecycleSnapshot:
    timestamp: float = 0.0
    active_signals: list[SignalTrace] = field(default_factory=list)
    drifting_signals: list[SignalTrace] = field(default_factory=list)
    total_tracked: int = 0
    transitioning: list[str] = field(default_factory=list)  # сигналы, сменившие стадию


class SignalLifecycleEngine:
    """
    Отслеживает жизненный цикл и уверенность сигналов.
    """

    def __init__(self):
        self._traces: dict[tuple[str, str], SignalTrace] = {}  # (signal_name, symbol) → Trace
        self._stage_history: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)  # stage changes
        self._last_snapshot: LifecycleSnapshot | None = None

    @property
    def last_snapshot(self) -> LifecycleSnapshot | None:
        return self._last_snapshot

    def update(self, result: SignalResult):
        """Обновить трейс сигнала."""
        key = (result.signal_name, result.symbol)
        now = time.time()

        if key not in self._traces:
            self._traces[key] = SignalTrace(
                signal_name=result.signal_name,
                symbol=result.symbol,
                first_seen=now,
                last_seen=now,
                direction=result.direction,
                max_score=result.score,
                min_score=result.score,
            )

        trace = self._traces[key]
        trace.last_seen = now
        trace.score_history.append((now, result.score))
        trace.detection_count += 1
        trace.direction = result.direction

        # Сохраняем до 30 последних точек
        if len(trace.score_history) > 30:
            trace.score_history = trace.score_history[-30:]

        # Max/min
        trace.max_score = max(trace.max_score, result.score)
        trace.min_score = min(trace.min_score, result.score)

        # Наклон (последние 3 точки)
        if len(trace.score_history) >= 3:
            recent = trace.score_history[-3:]
            x = list(range(len(recent)))
            y = [s for _, s in recent]
            n = len(x)
            denom = (n * sum(xi ** 2 for xi in x) - sum(x) ** 2)
            if n > 1 and denom != 0:
                trace.score_slope = (n * sum(x[i] * y[i] for i in range(n)) - sum(x) * sum(y)) / denom
            else:
                trace.score_slope = 0.0

        # Определяем стадию
        old_stage = trace.current_stage
        age = now - trace.first_seen

        if age > LIFECYCLE_DEAD_AFTER:
            trace.current_stage = "dead"
        elif trace.score_slope > 5:
            trace.current_stage = "growing"
        elif age > LIFECYCLE_CONFIRMED_MIN and abs(trace.score_slope) <= 5:
            trace.current_stage = "confirmed"
        elif trace.score_slope < -5:
            trace.current_stage = "weakening"
        elif age <= 120:  # 2 минуты
            trace.current_stage = "born"
        else:
            trace.current_stage = "confirmed"

        # Логируем смену стадии
        if old_stage != trace.current_stage:
            self._stage_history[key].append((trace.current_stage, now))
            logger.info("[lifecycle] %s %s: %s → %s (score %.0f, slope %.1f)",
                        result.symbol, result.signal_name, old_stage, trace.current_stage,
                        result.score, trace.score_slope)

        # Confidence Drift
        trace.drift_detected = self._check_drift(trace, now)

    def _check_drift(self, trace: SignalTrace, now: float) -> bool:
        """Проверить, есть ли дрифт уверенности."""
        if len(trace.score_history) < 3:
            return False

        # Смотрим последние DRIFT_WINDOW_MINUTES
        cutoff = now - DRIFT_WINDOW_MINUTES * 60
        window = [(ts, s) for ts, s in trace.score_history if ts >= cutoff]
        if len(window) < 2:
            return False

        initial_score = window[0][1]
        current_score = window[-1][1]

        if initial_score <= 0:
            return False

        drop_pct = (initial_score - current_score) / initial_score * 100
        if drop_pct >= DRIFT_THRESHOLD and current_score < trace.max_score * 0.8:
            logger.info("[drift] %s %s: %.0f → %.0f (%.0f%% drop in %d min)",
                        trace.symbol, trace.signal_name, initial_score, current_score,
                        drop_pct, DRIFT_WINDOW_MINUTES)
            return True

        return False

    def snapshot(self, now: float | None = None) -> LifecycleSnapshot:
        """Создать снэпшот текущего состояния."""
        now = now or time.time()

        # Чистим мёртвые
        to_remove = []
        for key, trace in self._traces.items():
            if now - trace.last_seen > LIFECYCLE_DEAD_AFTER * 2:
                to_remove.append(key)
        for key in to_remove:
            del self._traces[key]

        active = []
        drifting = []
        transitioning = []

        for key, trace in self._traces.items():
            sig_name, sym = key
            age = now - trace.first_seen
            if age > LIFECYCLE_DEAD_AFTER * 2:
                continue
            active.append(trace)
            if trace.drift_detected:
                drifting.append(trace)

            # Проверяем недавнюю смену стадии
            stage_history = self._stage_history.get(key, [])
            if stage_history:
                last_stage, last_time = stage_history[-1]
                if now - last_time < 300:  # последние 5 мин
                    transitioning.append(f"{trace.symbol} {trace.signal_name}: {trace.current_stage}")

        # Сортируем: сначала дрифт, потом угасающие
        active.sort(key=lambda t: t.score_slope if t.current_stage == "weakening" else 999)

        snap = LifecycleSnapshot(
            timestamp=now,
            active_signals=active,
            drifting_signals=drifting,
            total_tracked=len(active),
            transitioning=transitioning,
        )
        self._last_snapshot = snap
        return snap

    def to_signal(self) -> SignalResult | None:
        """Сигнал о дрифте уверенности."""
        snap = self.snapshot()
        if not snap.drifting_signals:
            return None

        # Берём худший дрифт
        worst = max(snap.drifting_signals, key=lambda t: abs(t.score_slope))
        if not worst.score_history:
            return None

        initial_score = worst.score_history[0][1]
        current_score = worst.score_history[-1][1]
        drop = (initial_score - current_score) / max(initial_score, 1) * 100
        duration = int((worst.score_history[-1][0] - worst.score_history[0][0]) / 60)

        score = min(abs(drop) * 1.5, 80)
        desc = f"⚠ Уверенность падает: {worst.symbol} {worst.signal_name} {initial_score:.0f} → {current_score:.0f} за {duration} мин"

        return SignalResult(
            signal_name="confidence_drift",
            symbol=worst.symbol,
            exchange="bybit",
            score=round(score, 0),
            direction=worst.direction,
            meta={
                "signal_name": worst.signal_name,
                "initial_score": initial_score,
                "current_score": current_score,
                "drop_pct": round(drop, 0),
                "duration_min": duration,
                "stage": worst.current_stage,
                "slope": round(worst.score_slope, 2),
                "description": desc,
            },
            ts=time.time(),
            cooldown=3600,
        )


_lifecycle_engine: SignalLifecycleEngine | None = None


def get_lifecycle_engine() -> SignalLifecycleEngine:
    global _lifecycle_engine
    if _lifecycle_engine is None:
        _lifecycle_engine = SignalLifecycleEngine()
    return _lifecycle_engine
