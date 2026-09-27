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
"""Verify bounded metrics registration, snapshots, exposition, and diagnostics export."""

import pytest

from orbit import Application, ApplicationConfig
from orbit.diagnostics import MetricSnapshot, MetricsRegistry


def test_metrics_registry_snapshots_counters_gauges_and_histograms():
    metrics = MetricsRegistry()
    counter = metrics.counter("requests_total", labels=("method",))
    gauge = metrics.gauge("workers", labels=("pool",))
    histogram = metrics.histogram("latency", buckets=(1.0, 2.0))
    counter.inc(method="GET")
    counter.inc(2, method="GET")
    gauge.set(3, pool="default")
    histogram.observe(1.5)

    snapshots = {item.name + str(item.labels): item for item in metrics.snapshots()}
    assert snapshots["requests_total{'method': 'GET'}"].value == 3
    assert snapshots["workers{'pool': 'default'}"].value == 3
    latency = snapshots["latency{}"]
    assert latency.count == 1 and latency.sum == 1.5
    assert latency.buckets == {1.0: 0, 2.0: 1}
    with pytest.raises(TypeError, match="immutable"):
        latency.labels["method"] = "POST"
    with pytest.raises(TypeError, match="immutable"):
        latency.buckets[1.0] = 99


def test_metric_snapshots_validate_exporter_boundaries() -> None:
    snapshot = MetricSnapshot(
        "latency",
        "histogram",
        {"route": "/health"},
        1.5,
        count=1,
        sum=1.5,
        buckets={1.0: 0, 2.0: 1},
    )
    assert snapshot.value == 1.5

    with pytest.raises(ValueError, match="names"):
        MetricSnapshot("bad name", "gauge", {}, 1)
    with pytest.raises(ValueError, match="kind"):
        MetricSnapshot("metric", "unknown", {}, 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="kind"):
        MetricSnapshot("metric", [], {}, 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite"):
        MetricSnapshot("metric", "gauge", {}, float("nan"))
    with pytest.raises(ValueError, match="counts"):
        MetricSnapshot("metric", "gauge", {}, 1, count=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="strictly increasing"):
        MetricSnapshot(
            "metric",
            "histogram",
            {},
            1,
            count=1,
            sum=1,
            buckets={2.0: 1, 1.0: 1},
        )
    with pytest.raises(ValueError, match="Only histogram"):
        MetricSnapshot("metric", "gauge", {}, 1, buckets={1.0: 1})
    with pytest.raises(ValueError, match="cannot exceed"):
        MetricSnapshot("metric", "histogram", {}, 1, count=1, buckets={1.0: 2})
    with pytest.raises(ValueError, match="cumulative"):
        MetricSnapshot(
            "metric",
            "histogram",
            {},
            1,
            count=2,
            buckets={1.0: 2, 2.0: 1},
        )
    with pytest.raises(ValueError, match="reserved"):
        MetricSnapshot("metric", "histogram", {"le": "1"}, 1, count=1, buckets={1.0: 1})


def test_metrics_registry_rejects_invalid_values_and_definitions():
    metrics = MetricsRegistry(max_series=1)
    counter = metrics.counter("count", labels=("kind",))
    counter.inc(kind="a")
    with pytest.raises(RuntimeError):
        counter.inc(kind="b")
    with pytest.raises(ValueError):
        metrics.histogram("bad", buckets=(2.0, 1.0))
    with pytest.raises(ValueError):
        MetricsRegistry(max_series="1")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        metrics.histogram("bad-type", buckets=("1",))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        MetricsRegistry(max_metrics=0)
    with pytest.raises(ValueError, match="1,000,000"):
        MetricsRegistry(max_series=1_000_001)
    with pytest.raises(ValueError, match="1,000,000"):
        MetricsRegistry(max_metrics=1_000_001)
    definitions = MetricsRegistry(max_metrics=1)
    definitions.counter("first")
    with pytest.raises(RuntimeError, match="definition limit"):
        definitions.gauge("second")
    with pytest.raises(ValueError, match="at most"):
        metrics.counter("too_many_labels", labels=tuple(f"label_{i}" for i in range(33)))
    with pytest.raises(TypeError, match="label names"):
        metrics.counter("non_string_label", labels=(1,))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        metrics.histogram("too_many_buckets", buckets=tuple(float(i) for i in range(1, 102)))
    with pytest.raises(ValueError, match="reserved"):
        metrics.histogram("reserved_label", labels=("le",))
    with pytest.raises(ValueError):
        counter.inc("1")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="strings"):
        counter.inc(kind=1)  # type: ignore[arg-type]


def test_metrics_registry_bounds_label_values():
    metrics = MetricsRegistry()
    counter = metrics.counter("requests", labels=("route",))
    with pytest.raises(ValueError, match="label values"):
        counter.inc(route="x" * 257)
    with pytest.raises(ValueError, match="label values"):
        counter.inc(route="ok\ninvalid")
    with pytest.raises(ValueError, match="label values"):
        counter.inc(route="ok\x7finvalid")


def test_metrics_registry_rejects_overflowing_and_unrepresentable_values() -> None:
    metrics = MetricsRegistry()
    counter = metrics.counter("requests_total")
    counter.inc(1e308)
    with pytest.raises(ValueError, match="cumulative"):
        counter.inc(1e308)
    with pytest.raises(ValueError, match="finite"):
        counter.inc(10**1000)

    histogram = metrics.histogram("latency", buckets=(1.0,))
    histogram.observe(1e308)
    with pytest.raises(ValueError, match="total"):
        histogram.observe(1e308)
    with pytest.raises(ValueError, match="finite"):
        histogram.observe(10**1000)


def test_metrics_registry_renders_prometheus_exposition():
    metrics = MetricsRegistry()
    metrics.counter("requests_total", labels=("method",)).inc(method="GET")
    metrics.histogram("latency", buckets=(1.0,)).observe(0.5)
    exposition = metrics.prometheus()
    assert "# TYPE requests_total counter" in exposition
    assert "# TYPE latency histogram" in exposition
    assert 'requests_total{method="GET"} 1.0' in exposition
    assert 'latency_bucket{le="1.0"} 1' in exposition
    assert "latency_count 1" in exposition


def test_diagnostics_export_is_json_and_validates_indent() -> None:
    import json

    app = Application(ApplicationConfig(name="diagnostic-export"))
    payload = json.loads(app.diagnostics.export(app, indent=2))
    assert payload["application"]["state"]["application_id"] == str(app.config.application.id)
    with pytest.raises(ValueError):
        app.diagnostics.export(app, indent=9)
    with pytest.raises(ValueError):
        app.diagnostics.export(app, indent=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        app.diagnostics.export(app, indent="2")  # type: ignore[arg-type]
