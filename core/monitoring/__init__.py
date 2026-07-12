"""
Мониторинг и метрики (Observability).

Компоненты:
- MetricsRegistry — единый реестр счётчиков/гистограмм/гейджей
- Healthcheck — HTTP endpoint /health (лёгкий, без внешних зависимостей)
- Prometheus-экспортёр — /metrics endpoint

Не требует prometheus_client — работает на встроенном asyncio HTTP server.
"""
from __future__ import annotations

from .registry import (MetricsRegistry, Counter, Gauge, Histogram,
                       get_metrics_registry, reset_metrics_registry)
from .health import Healthcheck, HealthStatus, HealthComponent, get_healthcheck
from .server import MetricsServer

__all__ = [
    "MetricsRegistry", "Counter", "Gauge", "Histogram",
    "get_metrics_registry", "reset_metrics_registry",
    "Healthcheck", "HealthStatus", "HealthComponent", "get_healthcheck",
    "MetricsServer",
]
