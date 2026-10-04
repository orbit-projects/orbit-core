# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Backend-neutral counters, gauges and histograms with bounded label cardinality."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from threading import RLock

from orbit._immutability import freeze_value
from orbit._limits import _MAX_CORE_CAPACITY, is_finite_number

_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]{0,254}$")
_MAX_LABELS = 32
_MAX_BUCKETS = 100


def _is_finite(value: object) -> bool:
    """Return false for non-numeric or overflow-sized values without leaking OverflowError."""
    return is_finite_number(value)


@dataclass(frozen=True)
class MetricSnapshot:
    """Validated, detached metric value suitable for exporters and diagnostics."""

    name: str
    kind: str
    labels: Mapping[str, str]
    value: float
    count: int = 0
    sum: float = 0.0
    buckets: Mapping[float, int] | None = None

    def __post_init__(self) -> None:
        """Validate exporter data before freezing nested mappings in the public snapshot."""
        if not isinstance(self.name, str) or _NAME.fullmatch(self.name) is None:
            raise ValueError("Metric snapshot names must be valid metric identifiers.")
        if not isinstance(self.kind, str) or self.kind not in {"counter", "gauge", "histogram"}:
            raise ValueError("Metric snapshot kind must be counter, gauge, or histogram.")
        if not isinstance(self.labels, Mapping):
            raise TypeError("Metric snapshot labels must be a mapping.")
        # Detach while validating: a custom mapping can misreport its length, so dict(self.labels)
        # must not materialize an oversized label set before the cardinality check.
        labels: dict[str, str] = {}
        for index, (name, value) in enumerate(self.labels.items(), start=1):
            if index > _MAX_LABELS:
                raise ValueError(
                    f"Metric snapshot labels must contain at most {_MAX_LABELS} entries."
                )
            if (
                not isinstance(name, str)
                or _NAME.fullmatch(name) is None
                or not isinstance(value, str)
                or len(value) > 256
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError("Metric snapshot labels must have valid names and text values.")
            labels[name] = value
        if not _is_finite(self.value) or not _is_finite(self.sum):
            raise ValueError("Metric snapshot values must be finite numbers.")
        if isinstance(self.count, bool) or not isinstance(self.count, int) or self.count < 0:
            raise ValueError("Metric snapshot counts must be nonnegative integers.")
        if self.kind in {"counter", "histogram"} and self.value < 0:
            raise ValueError("Counter and histogram snapshot values cannot be negative.")
        if self.sum < 0:
            raise ValueError("Metric snapshot sums cannot be negative.")
        try:
            numeric_value = float(self.value)
            total = float(self.sum)
        except (OverflowError, ValueError) as exc:
            raise ValueError(
                "Metric snapshot values must be representable finite numbers."
            ) from exc
        object.__setattr__(self, "value", numeric_value)
        object.__setattr__(self, "sum", total)
        if self.kind == "histogram":
            if not isinstance(self.buckets, Mapping):
                raise TypeError("Histogram snapshots require a bucket mapping.")
            bucket_values: dict[float, int] = {}
            bounds: list[float] = []
            bucket_counts: list[int] = []
            for index, (bound, bucket_count) in enumerate(self.buckets.items(), start=1):
                if index > _MAX_BUCKETS:
                    raise ValueError(f"Histogram snapshots support at most {_MAX_BUCKETS} buckets.")
                if not _is_finite(bound) or bound <= 0:
                    raise ValueError("Histogram snapshot bounds must be finite and positive.")
                if (
                    isinstance(bucket_count, bool)
                    or not isinstance(bucket_count, int)
                    or bucket_count < 0
                ):
                    raise ValueError("Histogram snapshot counts must be nonnegative integers.")
                numeric_bound = float(bound)
                bounds.append(numeric_bound)
                bucket_counts.append(bucket_count)
                bucket_values[numeric_bound] = bucket_count
            if not bucket_values:
                raise ValueError("Histogram snapshots require nonempty buckets.")
            if tuple(sorted(set(bounds))) != tuple(bounds):
                raise ValueError("Histogram snapshot bounds must be strictly increasing.")
            if any(count > self.count for count in bucket_counts):
                raise ValueError("Histogram bucket counts cannot exceed the total count.")
            if any(
                earlier > later
                for earlier, later in zip(bucket_counts, bucket_counts[1:], strict=False)
            ):
                raise ValueError("Histogram bucket counts must be cumulative and nondecreasing.")
        elif self.buckets is not None:
            raise ValueError("Only histogram snapshots may contain buckets.")
        object.__setattr__(self, "labels", freeze_value(labels))
        if self.kind == "histogram":
            object.__setattr__(self, "buckets", freeze_value(bucket_values))


class _Metric:
    def __init__(self, name: str, kind: str, label_names: tuple[str, ...]) -> None:
        self.name, self.kind, self.label_names = name, kind, label_names
        self.values: dict[tuple[tuple[str, str], ...], float] = {}

    def key(self, labels: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
        """Validate label names and return a deterministic immutable series key."""
        if set(labels) != set(self.label_names):
            raise ValueError(f"Labels for {self.name!r} must be {self.label_names!r}.")
        values: list[tuple[str, str]] = []
        for key in self.label_names:
            value = labels[key]
            if not isinstance(value, str):
                raise TypeError("Metric label values must be strings.")
            if len(value) > 256 or any(
                ord(character) < 32 or ord(character) == 127 for character in value
            ):
                raise ValueError("Metric label values must be at most 256 printable characters.")
            values.append((key, value))
        return tuple(values)


class MetricsRegistry:
    """Own bounded in-process metric series for Core and adapter instrumentation.

    The registry is deliberately backend-neutral. Updates are synchronized, label names are
    fixed when a metric is registered, and ``max_series`` prevents unbounded cardinality. Use
    :meth:`snapshots` to feed a separately installed exporter; Core does not select an exposition
    format, open a metrics endpoint, or choose an external telemetry vendor.
    """

    def __init__(self, *, max_series: int = 10_000, max_metrics: int = 1_000) -> None:
        if (
            isinstance(max_series, bool)
            or not isinstance(max_series, int)
            or not 1 <= max_series <= _MAX_CORE_CAPACITY
        ):
            raise ValueError("max_series must be positive and no more than 1,000,000.")
        if (
            isinstance(max_metrics, bool)
            or not isinstance(max_metrics, int)
            or not 1 <= max_metrics <= _MAX_CORE_CAPACITY
        ):
            raise ValueError("max_metrics must be positive and no more than 1,000,000.")
        self._max_series = max_series
        self._max_metrics = max_metrics
        self._metrics: dict[str, _Metric] = {}
        self._histograms: dict[str, tuple[float, ...]] = {}
        self._histogram_values: dict[
            str, dict[tuple[tuple[str, str], ...], tuple[int, float, dict[float, int]]]
        ] = {}
        self._lock = RLock()

    def _register(self, name: str, kind: str, labels: tuple[str, ...]) -> _Metric:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("Invalid metric name.")
        if not isinstance(labels, tuple) or len(labels) > _MAX_LABELS:
            raise ValueError(f"Metric labels must be a tuple of at most {_MAX_LABELS} names.")
        if any(not isinstance(label, str) for label in labels):
            raise TypeError("Metric label names must be strings.")
        if len(set(labels)) != len(labels) or any(not _NAME.fullmatch(label) for label in labels):
            raise ValueError("Metric labels must be unique and valid names.")
        existing = self._metrics.get(name)
        if existing is not None:
            if existing.kind != kind or existing.label_names != labels:
                raise ValueError(f"Metric {name!r} has an incompatible definition.")
            return existing
        if len(self._metrics) >= self._max_metrics:
            raise RuntimeError("Metric definition limit reached.")
        metric = _Metric(name, kind, labels)
        self._metrics[name] = metric
        return metric

    def counter(self, name: str, *, labels: tuple[str, ...] = ()) -> Counter:
        """Register or retrieve a monotonically increasing counter definition."""
        with self._lock:
            self._register(name, "counter", labels)
        return Counter(self, name)

    def gauge(self, name: str, *, labels: tuple[str, ...] = ()) -> Gauge:
        """Register or retrieve a point-in-time gauge definition."""
        with self._lock:
            self._register(name, "gauge", labels)
        return Gauge(self, name)

    def histogram(
        self,
        name: str,
        *,
        labels: tuple[str, ...] = (),
        buckets: tuple[float, ...] = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0, 30.0),
    ) -> Histogram:
        """Register or retrieve a histogram with immutable, increasing bucket bounds."""
        if (
            not isinstance(buckets, tuple)
            or not buckets
            or len(buckets) > _MAX_BUCKETS
            or any(not _is_finite(value) or value <= 0 for value in buckets)
        ):
            raise ValueError(
                f"Histogram buckets must be a tuple of 1-{_MAX_BUCKETS} finite positive values."
            )
        if tuple(sorted(set(buckets))) != buckets:
            raise ValueError("Histogram buckets must be strictly increasing.")
        with self._lock:
            self._register(name, "histogram", labels)
            current = self._histograms.get(name)
            if current is not None and current != buckets:
                raise ValueError(f"Histogram {name!r} has incompatible buckets.")
            self._histograms[name] = buckets
            self._histogram_values.setdefault(name, {})
        return Histogram(self, name)

    def _update(self, name: str, labels: Mapping[str, str], amount: float, *, mode: str) -> None:
        with self._lock:
            metric = self._metrics[name]
            key = metric.key(labels)
            if key not in metric.values and self._series_count() >= self._max_series:
                raise RuntimeError("Metric series limit reached.")
            updated = amount if mode == "set" else metric.values.get(key, 0.0) + amount
            if not _is_finite(updated):
                raise ValueError("Metric cumulative value must remain finite.")
            metric.values[key] = updated

    def _observe(self, name: str, labels: Mapping[str, str], value: float) -> None:
        if not _is_finite(value) or value < 0:
            raise ValueError("Histogram observations must be finite and nonnegative.")
        with self._lock:
            metric = self._metrics[name]
            key = metric.key(labels)
            series = self._histogram_values[name].get(key)
            if series is None:
                if self._series_count() >= self._max_series:
                    raise RuntimeError("Metric series limit reached.")
                series = (0, 0.0, dict.fromkeys(self._histograms[name], 0))
            count, total, buckets = series
            updated_total = total + value
            if not _is_finite(updated_total):
                raise ValueError("Metric histogram total must remain finite.")
            for bound in buckets:
                buckets[bound] += int(value <= bound)
            self._histogram_values[name][key] = (count + 1, updated_total, buckets)

    def _series_count(self) -> int:
        return sum(len(metric.values) for metric in self._metrics.values()) + sum(
            len(values) for values in self._histogram_values.values()
        )

    def snapshots(self) -> tuple[MetricSnapshot, ...]:
        """Return detached values that can be serialized without holding the registry lock."""
        with self._lock:
            result: list[MetricSnapshot] = []
            for name, metric in self._metrics.items():
                if metric.kind == "histogram":
                    for key, (count, total, buckets) in self._histogram_values[name].items():
                        result.append(
                            MetricSnapshot(
                                name, metric.kind, dict(key), total, count, total, dict(buckets)
                            )
                        )
                else:
                    for key, value in metric.values.items():
                        result.append(MetricSnapshot(name, metric.kind, dict(key), value))
            return tuple(result)


class Counter:
    """Handle updates to one registry-owned counter definition."""

    def __init__(self, registry: MetricsRegistry, name: str) -> None:
        self._registry, self.name = registry, name

    def inc(self, amount: float = 1, **labels: str) -> None:
        """Add a finite non-negative amount to the labeled series."""
        if not _is_finite(amount) or amount < 0:
            raise ValueError("Counter increments must be finite and nonnegative.")
        self._registry._update(self.name, labels, amount, mode="add")


class Gauge:
    """Handle updates to one registry-owned gauge definition."""

    def __init__(self, registry: MetricsRegistry, name: str) -> None:
        self._registry, self.name = registry, name

    def set(self, value: float, **labels: str) -> None:
        """Set a labeled series to a finite value."""
        if not _is_finite(value):
            raise ValueError("Gauge values must be finite.")
        self._registry._update(self.name, labels, value, mode="set")

    def inc(self, amount: float = 1, **labels: str) -> None:
        """Add a finite amount to a labeled series."""
        if not _is_finite(amount):
            raise ValueError("Gauge increments must be finite.")
        self._registry._update(self.name, labels, amount, mode="add")


class Histogram:
    """Handle observations for one registry-owned histogram definition."""

    def __init__(self, registry: MetricsRegistry, name: str) -> None:
        self._registry, self.name = registry, name

    def observe(self, value: float, **labels: str) -> None:
        """Record a finite, non-negative observation in the labeled series."""
        self._registry._observe(self.name, labels, value)


__all__ = ["Counter", "Gauge", "Histogram", "MetricSnapshot", "MetricsRegistry"]
