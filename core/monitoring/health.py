"""
Healthcheck — лёгкий endpoint для проверки состояния системы.

Компоненты:
- HealthComponent: один компонент (state engine, risk engine, etc.)
- HealthStatus: результат по всем компонентам
"""
from __future__ import annotations

import dataclasses
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthComponent:
    """Состояние одного компонента."""
    name: str
    status: HealthStatus = HealthStatus.HEALTHY
    message: str = ""
    last_check: float = 0.0
    uptime: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "last_check": round(self.last_check, 1),
            "uptime": round(self.uptime, 1),
            **self.extra,
        }


class Healthcheck:
    """Глобальный healthcheck. Компоненты регистрируют свои check-функции."""

    def __init__(self):
        self._start_time = time.time()
        self._checks: dict[str, Callable[[], HealthComponent]] = {}
        self._cache: dict[str, HealthComponent] = {}
        self._cache_ttl = 5.0
        self._last_cache_clear = time.time()

    def register(self, name: str, check_fn: Callable[[], HealthComponent]):
        """Зарегистрировать check-функцию для компонента."""
        self._checks[name] = check_fn
        logger.info("[health] Registered component: %s", name)

    def check_all(self) -> list[HealthComponent]:
        """Проверить все компоненты (с кэшированием на cache_ttl секунд)."""
        now = time.time()
        results = []

        # Очистка кэша
        if now - self._last_cache_clear > self._cache_ttl:
            self._cache.clear()
            self._last_cache_clear = now

        for name, fn in self._checks.items():
            if name in self._cache:
                results.append(self._cache[name])
                continue
            try:
                component = fn()
            except Exception as e:
                component = HealthComponent(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    message=str(e),
                    last_check=now,
                )
                logger.error("[health] %s check failed: %s", name, e)
            component.last_check = now
            component.uptime = now - self._start_time
            self._cache[name] = component
            results.append(component)

        return results

    @property
    def overall(self) -> HealthStatus:
        """Самый плохой статус среди всех компонентов."""
        components = self.check_all()
        statuses = {c.status for c in components}
        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        if HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY

    def to_dict(self) -> dict:
        return {
            "status": self.overall.value,
            "uptime": round(time.time() - self._start_time, 1),
            "components": [c.to_dict() for c in self.check_all()],
        }


# Singleton
_healthcheck: Healthcheck | None = None


def get_healthcheck() -> Healthcheck:
    global _healthcheck
    if _healthcheck is None:
        _healthcheck = Healthcheck()
    return _healthcheck
