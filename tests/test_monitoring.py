"""Tests for Observability — MetricsRegistry, Healthcheck, MetricsServer."""
from __future__ import annotations

import pytest

from core.monitoring import (
    MetricsRegistry, Counter, Gauge, Histogram,
    get_metrics_registry, reset_metrics_registry,
    Healthcheck, HealthStatus, HealthComponent, get_healthcheck,
    MetricsServer,
)


class TestCounter:
    def test_inc(self):
        c = Counter("test_total")
        c.inc()
        assert c.value == 1.0

    def test_inc_multiple(self):
        c = Counter("test_total")
        c.inc(5)
        assert c.value == 5.0

    def test_label_key(self):
        c = Counter("test_total", labels={"exchange": "bybit"})
        assert "bybit" in c.label_key()


class TestGauge:
    def test_set(self):
        g = Gauge("positions_open")
        g.set(3)
        assert g.value == 3.0

    def test_inc_dec(self):
        g = Gauge("positions_open")
        g.set(0)
        g.inc()
        assert g.value == 1.0
        g.dec()
        assert g.value == 0.0


class TestHistogram:
    def test_observe(self):
        h = Histogram("latency_ms", buckets=[0.1, 0.5, 1.0])
        h.observe(0.3)
        assert h.count == 1
        assert 0.2 < h.sum < 0.4

    def test_multiple_observations(self):
        h = Histogram("latency_ms")
        h.observe(0.3)
        h.observe(0.7)
        h.observe(1.5)
        assert h.count == 3


class TestMetricsRegistry:
    def setup_method(self):
        reset_metrics_registry()

    def test_counter(self):
        reg = get_metrics_registry()
        c = reg.counter("signals_total")
        c.inc()
        assert c.value == 1.0

    def test_gauge(self):
        reg = get_metrics_registry()
        g = reg.gauge("positions_open")
        g.set(5)
        assert g.value == 5.0

    def test_histogram(self):
        reg = get_metrics_registry()
        h = reg.histogram("compute_ms")
        h.observe(1.5)
        assert h.count == 1

    def test_prometheus_output(self):
        reg = get_metrics_registry()
        reg.inc("signals_total")
        reg.gauge_set("positions_open", 5)
        h = reg.histogram("compute_ms")
        h.observe(0.5)

        text = reg.prometheus_text()
        assert "# TYPE" in text
        assert "signals_total" in text
        assert "positions_open" in text
        assert "compute_ms_bucket" in text

    def test_snapshot(self):
        reg = get_metrics_registry()
        reg.inc("signals_total")
        snap = reg.snapshot()
        assert "signals_total" in snap


class TestHealthcheck:
    def test_healthy(self):
        hc = Healthcheck()
        hc.register("test", lambda: HealthComponent("test", HealthStatus.HEALTHY))
        assert hc.overall == HealthStatus.HEALTHY

    def test_unhealthy(self):
        hc = Healthcheck()
        hc.register("ok", lambda: HealthComponent("ok", HealthStatus.HEALTHY))
        hc.register("fail", lambda: HealthComponent("fail", HealthStatus.UNHEALTHY, "broken"))
        assert hc.overall == HealthStatus.UNHEALTHY

    def test_error_handler(self):
        hc = Healthcheck()
        hc.register("broken", lambda: (_ for _ in ()).throw(RuntimeError("fail")))
        comps = hc.check_all()
        assert comps[0].status == HealthStatus.UNHEALTHY
        assert "fail" in comps[0].message

    def test_to_dict(self):
        hc = Healthcheck()
        hc.register("test", lambda: HealthComponent("test", HealthStatus.HEALTHY))
        d = hc.to_dict()
        assert "status" in d
        assert "components" in d
