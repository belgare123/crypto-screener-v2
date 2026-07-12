"""
MetricsRegistry — in-memory реестр метрик без внешних зависимостей.

Совместим с Prometheus exposition format:
- Counter: только инкремент
- Gauge: set/increment/decrement
- Histogram: observe() + buckets

Prometheus text format: /metrics endpoint выводит в формате:
# TYPE <name> <type>
# HELP <name> <doc>
<name>{labels} <value>
"""
from __future__ import annotations

import json
import threading
import time
from collections import defaultdict
from typing import Any


class _Metric:
    """Базовый класс метрики."""
    def __init__(self, name: str, doc: str = "", labels: dict[str, str] | None = None):
        self.name = name
        self.doc = doc or ""
        self.labels = labels or {}
        self.created = time.time()

    def label_key(self) -> str:
        return json.dumps(self.labels, sort_keys=True) if self.labels is not None else "{}"


class Counter(_Metric):
    _type = "counter"

    def __init__(self, name: str, doc: str = "", labels: dict[str, str] | None = None):
        super().__init__(name, doc, labels)
        self._value = 0.0

    def inc(self, amount: float = 1.0):
        self._value += amount

    @property
    def value(self) -> float:
        return self._value


class Gauge(_Metric):
    _type = "gauge"

    def __init__(self, name: str, doc: str = "", labels: dict[str, str] | None = None):
        super().__init__(name, doc, labels)
        self._value = 0.0

    def set(self, value: float):
        self._value = value

    def inc(self, amount: float = 1.0):
        self._value += amount

    def dec(self, amount: float = 1.0):
        self._value -= amount

    @property
    def value(self) -> float:
        return self._value


class Histogram(_Metric):
    _type = "histogram"

    def __init__(self, name: str, doc: str = "",
                 buckets: list[float] | None = None,
                 labels: dict[str, str] | None = None):
        super().__init__(name, doc, labels)
        self.buckets = sorted(buckets or [0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0])
        self._counts = {b: 0 for b in self.buckets}
        self._counts["+Inf"] = 0
        self._sum = 0.0
        self._total_count = 0

    def observe(self, value: float):
        self._sum += value
        self._total_count += 1
        for b in self.buckets:
            if value <= b:
                self._counts[b] += 1
        self._counts["+Inf"] += 1

    @property
    def sum(self) -> float:
        return self._sum

    @property
    def count(self) -> int:
        return self._total_count

    def prometheus_lines(self, full_name: str) -> list[str]:
        lines = []
        le_key = json.dumps(self.labels, sort_keys=True) if self.labels else "{}"
        le_labels = f"{le_key[:-1]}, \"le\": \"+Inf\"}}" if self.labels else "{\"le\": \"+Inf\"}"
        for b in self.buckets + ["+Inf"]:
            b_labels = (f"{le_key[:-1]}, \"le\": \"{b}\"}}" if self.labels
                        else f"{{\"le\": \"{b}\"}}")
            lines.append(f"{full_name}_bucket{b_labels} {self._counts[b]}")
        lines.append(f"{full_name}_sum{le_key} {self._sum:.4f}")
        lines.append(f"{full_name}_count{le_key} {self._total_count}")
        return lines


class MetricsRegistry:
    """Потокобезопасный реестр метрик."""

    def __init__(self):
        self._lock = threading.Lock()
        self._metrics: dict[str, list[_Metric]] = defaultdict(list)  # name → variants by labels

    # --- Counters ---

    def counter(self, name: str, doc: str = "",
                labels: dict[str, str] | None = None) -> Counter:
        """Получить или создать Counter."""
        with self._lock:
            key = (name, json.dumps(labels or {}, sort_keys=True))
            for m in self._metrics[name]:
                if m.label_key() == json.dumps(labels or {}, sort_keys=True):
                    return m  # type: ignore
            c = Counter(name, doc, labels)
            self._metrics[name].append(c)
            return c

    def inc(self, name: str, amount: float = 1.0, labels: dict[str, str] | None = None):
        """Увеличить Counter по имени (создать если нет)."""
        self.counter(name, labels=labels).inc(amount)

    # --- Gauges ---

    def gauge(self, name: str, doc: str = "",
              labels: dict[str, str] | None = None) -> Gauge:
        with self._lock:
            for m in self._metrics[name]:
                if m.label_key() == json.dumps(labels or {}, sort_keys=True):
                    return m  # type: ignore
            g = Gauge(name, doc, labels)
            self._metrics[name].append(g)
            return g

    def gauge_set(self, name: str, value: float, labels: dict[str, str] | None = None):
        self.gauge(name, labels=labels).set(value)

    # --- Histograms ---

    def histogram(self, name: str, doc: str = "",
                  buckets: list[float] | None = None,
                  labels: dict[str, str] | None = None) -> Histogram:
        with self._lock:
            key = (name, json.dumps(labels or {}, sort_keys=True))
            for m in self._metrics[name]:
                if m.label_key() == json.dumps(labels or {}, sort_keys=True):
                    return m  # type: ignore
            h = Histogram(name, doc, buckets, labels)
            self._metrics[name].append(h)
            return h

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None):
        self.histogram(name, labels=labels).observe(value)

    # --- Prometheus text format ---

    def prometheus_text(self) -> str:
        """Экспорт в Prometheus exposition format."""
        lines: list[str] = []
        with self._lock:
            for name, variants in self._metrics.items():
                if not variants:
                    continue
                first = variants[0]
                # TYPE header
                lines.append(f"# TYPE {first.name} {first._type}")
                if first.doc:
                    lines.append(f"# HELP {first.name} {first.doc}")

                full_name = name.replace(".", "_").replace("-", "_")

                if first._type == "histogram":
                    for h in variants:  # type: Histogram
                        label_pairs = ",".join('{}="{}"'.format(k, v) for k, v in h.labels.items())
                        hist_labels = "{" + label_pairs + "}" if label_pairs else ""
                        hn = f"{full_name}{hist_labels}"
                        lines.extend(h.prometheus_lines(hn))
                else:
                    for m in variants:
                        label_pairs = ",".join('{}="{}"'.format(k, v) for k, v in m.labels.items())
                        ls = "{" + label_pairs + "}" if label_pairs else ""
                        if first._type == "counter":
                            lines.append(f"{full_name}{ls} {m.value:.0f}")  # type: ignore
                        elif first._type == "gauge":
                            lines.append(f"{full_name}{ls} {m.value:.4f}")  # type: ignore

        return "\n".join(lines) + "\n"

    def snapshot(self) -> dict:
        """Слепок всех метрик (для healthcheck / debug)."""
        data = {}
        with self._lock:
            for name, variants in self._metrics.items():
                vals = []
                for m in variants:
                    if isinstance(m, Histogram):
                        vals.append({"labels": m.labels, "count": m.count, "sum": round(m.sum, 4)})
                    else:
                        vals.append({"labels": m.labels, "value": m.value})
                data[name] = vals
        return data


# Singleton
_registry: MetricsRegistry | None = None


def get_metrics_registry() -> MetricsRegistry:
    global _registry
    if _registry is None:
        _registry = MetricsRegistry()
    return _registry


def reset_metrics_registry():
    global _registry
    _registry = None
