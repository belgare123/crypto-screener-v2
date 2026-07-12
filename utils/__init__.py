"""Утилиты: форматирование, таймштампы, helpers."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


def now_ts() -> float:
    """Текущий unix timestamp (ms)."""
    return time.time() * 1000


def ts_to_str(ts: float) -> str:
    """ms timestamp → читаемая строка."""
    dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def fmt_usdt(value: float | Decimal | None) -> str:
    """Форматирование USDT: $1.2M, $350K, $12.5."""
    if value is None:
        return "—"
    v = float(value)
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:,.1f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:,.1f}K"
    return f"${v:,.2f}"


def fmt_percent(value: float | None) -> str:
    """+12.5% / -3.2%."""
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.1f}%"


class SignalCooldown:
    """
    Антиспам: не даёт отправить тот же сигнал по той же монете
    раньше cooldown-периода.
    """

    def __init__(self, default_cooldown: float = 1800):
        self._last: dict[tuple[str, str], tuple[float, float]] = {}  # (signal, symbol) -> (last_ts, last_score)
        self._default = default_cooldown

    def can_send(self, signal_name: str, symbol: str, score: float, cooldown: float | None = None) -> bool:
        now = time.time()
        key = (signal_name, symbol)
        cd = cooldown or self._default

        if key not in self._last:
            self._last[key] = (now, score)
            return True

        last_ts, last_score = self._last[key]

        # Если время не вышло — блок
        if now - last_ts < cd:
            return False

        # degrade mode: не слать, если score ниже предыдущего
        if score <= last_score:
            return False

        self._last[key] = (now, score)
        return True
