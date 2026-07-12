"""
Session Engine — определяет торговые сессии и транслирует события.

Режимы:
  ASIA        00:00-09:00 UTC   — диапазон, низкая волатильность
  LONDON      08:00-17:00 UTC   — начало активности, трендовые движения
  NY          13:00-22:00 UTC   — высокая волатильность, новости
  OVERLAP_LN  13:00-17:00 UTC   — London + NY = пик ликвидности

Интеграция с Adaptive Thresholds:
  SessionEngine заменяет get_current_session() в core/adaptive.py
  и публикует события смены сессии в шину данных.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Типы сессий
# ──────────────────────────────────────────────

class SessionType(str, Enum):
    ASIA = "asia"               # 00:00-09:00 UTC
    ASIA_LONDON_OVERLAP = "asia_london"  # 08:00-09:00 UTC
    LONDON = "london"           # 09:00-17:00 UTC (без оверлапа)
    LONDON_NY_OVERLAP = "overlap_lon_ny"  # 13:00-17:00 UTC
    NY = "ny"                   # 17:00-22:00 UTC (без оверлапа)


# ──────────────────────────────────────────────
#  События сессии
# ──────────────────────────────────────────────

class SessionEvent(Enum):
    OPEN = "open"          # сессия началась
    ACTIVE = "active"      # продолжается (каждую минуту)
    CLOSE = "close"        # сессия заканчивается


# ──────────────────────────────────────────────
#  Профили сессий: типичная волатильность и объём
# ──────────────────────────────────────────────

@dataclass
class SessionProfile:
    name: str
    typical_volatility_pct: float  # ожидаемый ATR% в этой сессии
    volume_share_pct: float        # доля дневного объёма
    momentum_weight: float         # вес для сигналов momentum (0-1)
    reversal_probability: float    # вероятность разворота (0-1)
    description: str

SESSION_PROFILES: dict[SessionType, SessionProfile] = {
    SessionType.ASIA: SessionProfile(
        name="Asia",
        typical_volatility_pct=0.3,
        volume_share_pct=12,
        momentum_weight=0.5,
        reversal_probability=0.3,
        description="Азиатская сессия: низкая волатильность, диапазон, меньше ложных пробоев",
    ),
    SessionType.ASIA_LONDON_OVERLAP: SessionProfile(
        name="Asia-London Overlap",
        typical_volatility_pct=0.6,
        volume_share_pct=8,
        momentum_weight=0.7,
        reversal_probability=0.4,
        description="Пересечение Азия-Лондон: начало разогрева, первые трендовые движения",
    ),
    SessionType.LONDON: SessionProfile(
        name="London",
        typical_volatility_pct=1.0,
        volume_share_pct=32,
        momentum_weight=1.0,
        reversal_probability=0.5,
        description="Лондонская сессия: высокая активность, трендовые движения",
    ),
    SessionType.LONDON_NY_OVERLAP: SessionProfile(
        name="London-NY Overlap",
        typical_volatility_pct=1.5,
        volume_share_pct=28,
        momentum_weight=1.2,
        reversal_probability=0.6,
        description="Пересечение Лондон-Нью-Йорк: пик ликвидности, максимальная волатильность",
    ),
    SessionType.NY: SessionProfile(
        name="New York",
        typical_volatility_pct=1.2,
        volume_share_pct=20,
        momentum_weight=1.1,
        reversal_probability=0.7,
        description="Нью-Йоркская сессия: волатильность, новости, развороты",
    ),
}

# Множители порогов по сессиям (используются AdaptiveThresholds)
SESSION_THRESHOLD_MULTIPLIERS: dict[SessionType, float] = {
    SessionType.ASIA: 1.3,
    SessionType.ASIA_LONDON_OVERLAP: 1.1,
    SessionType.LONDON: 1.0,
    SessionType.LONDON_NY_OVERLAP: 0.85,
    SessionType.NY: 0.95,
}


# ──────────────────────────────────────────────
#  Детектор сессии
# ──────────────────────────────────────────────

def detect_session(utc_hour: float) -> SessionType:
    """Определить сессию по UTC часу (с дробной частью)."""
    if 0 <= utc_hour < 8:
        return SessionType.ASIA
    elif 8 <= utc_hour < 9:
        return SessionType.ASIA_LONDON_OVERLAP
    elif 9 <= utc_hour < 13:
        return SessionType.LONDON
    elif 13 <= utc_hour < 17:
        return SessionType.LONDON_NY_OVERLAP
    elif 17 <= utc_hour < 22:
        return SessionType.NY
    else:  # 22-24
        return SessionType.ASIA


def utc_hour_now() -> float:
    """Текущий час UTC с дробной частью."""
    now = time.gmtime()
    return now.tm_hour + now.tm_min / 60.0


def session_progress(utc_hour: float, session: SessionType) -> float:
    """Доля сессии от начала до конца (0.0 — 1.0)."""
    ranges = {
        SessionType.ASIA: (0, 8),
        SessionType.ASIA_LONDON_OVERLAP: (8, 9),
        SessionType.LONDON: (9, 13),
        SessionType.LONDON_NY_OVERLAP: (13, 17),
        SessionType.NY: (17, 22),
    }
    lo, hi = ranges.get(session, (0, 24))
    clamped = max(lo, min(utc_hour, hi))
    return (clamped - lo) / (hi - lo) if hi > lo else 0.0


# ──────────────────────────────────────────────
#  SessionEngine — трекер с событиями
# ──────────────────────────────────────────────

@dataclass
class SessionState:
    current: SessionType = SessionType.ASIA
    previous: SessionType | None = None
    started_at: float = 0.0     # unix timestamp начала сессии
    progress: float = 0.0       # 0..1
    hour: float = 0.0
    uptime: int = 0             # сколько минут в этой сессии


class SessionEngine:
    """
    Определяет текущую сессию и отслеживает переходы.

    Использование в сигналах:
        session = session_engine.current
        profile = session_engine.get_profile()

    Для подписки на события:
        bus.subscribe('session.*')
    """

    def __init__(self):
        self._state = SessionState()
        self._initialized = False
        self._listeners: list[callable] = []

    # ── Свойства ──

    @property
    def current(self) -> SessionType:
        return self._state.current

    @property
    def previous(self) -> SessionType | None:
        return self._state.previous

    @property
    def progress(self) -> float:
        return self._state.progress

    @property
    def session_name(self) -> str:
        return self.get_profile().name

    # ── API ──

    def get_profile(self, session: SessionType | None = None) -> SessionProfile:
        """Профиль сессии: типичная волатильность, вес и т.д."""
        s = session or self._state.current
        return SESSION_PROFILES.get(s, SESSION_PROFILES[SessionType.ASIA])

    def get_threshold_multiplier(self, session: SessionType | None = None) -> float:
        """Множитель порогов для AdaptiveThresholds."""
        s = session or self._state.current
        return SESSION_THRESHOLD_MULTIPLIERS.get(s, 1.0)

    def get_info(self) -> dict[str, Any]:
        """Полная информация о текущей сессии."""
        profile = self.get_profile()
        return {
            "session": self._state.current.value,
            "name": profile.name,
            "progress": round(self._state.progress, 2),
            "uptime_min": self._state.uptime,
            "hour_utc": round(self._state.hour, 2),
            "volatility_pct": profile.typical_volatility_pct,
            "volume_share_pct": profile.volume_share_pct,
            "momentum_weight": profile.momentum_weight,
            "description": profile.description,
        }

    # ── Основной цикл ──

    async def tick(self):
        """Проверить сессию (вызывается раз в минуту)."""
        hour = utc_hour_now()
        new_session = detect_session(hour)

        if not self._initialized:
            self._state.current = new_session
            self._state.started_at = time.time()
            self._state.hour = hour
            self._state.progress = session_progress(hour, new_session)
            self._initialized = True
            await self._notify(SessionEvent.OPEN)
            return

        self._state.hour = hour
        self._state.progress = session_progress(hour, new_session)
        self._state.uptime += 1

        if new_session != self._state.current:
            # Смена сессии
            old = self._state.current
            self._state.previous = old
            self._state.current = new_session
            self._state.started_at = time.time()
            self._state.uptime = 0
            logger.info("Session change: %s → %s", old.value, new_session.value)
            await self._notify(SessionEvent.CLOSE, old)
            await self._notify(SessionEvent.OPEN)
        else:
            await self._notify(SessionEvent.ACTIVE)

    def add_listener(self, fn: callable):
        """Подписаться на события сессии."""
        self._listeners.append(fn)

    async def _notify(self, event: SessionEvent, session: SessionType | None = None):
        """Разослать событие подписчикам."""
        info = self.get_info()
        s = session or self._state.current
        payload = {
            "event": event.value,
            "session": s.value,
            "session_name": self.get_profile(s).name,
            "info": info,
        }
        for fn in self._listeners:
            try:
                if asyncio.iscoroutinefunction(fn):
                    await fn(payload)
                else:
                    fn(payload)
            except Exception as e:
                logger.error("Session listener error: %s", e)

        # Логируем смену или раз в 10 минут при активной сессии
        if event == SessionEvent.OPEN:
            logger.info("Session started: %s at %.2f UTC", info["name"], info["hour_utc"])


# ──────────────────────────────────────────────
#  Экспорт для AdaptiveThresholds
# ──────────────────────────────────────────────

_engine: SessionEngine | None = None


def get_session_engine() -> SessionEngine:
    global _engine
    if _engine is None:
        _engine = SessionEngine()
    return _engine


def get_current_session_type() -> SessionType:
    """Для замены placeholder-функции в core/adaptive.py."""
    engine = get_session_engine()
    if engine.current:
        return engine.current
    return detect_session(utc_hour_now())
