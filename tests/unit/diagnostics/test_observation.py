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
"""Bounded operational telemetry, observer isolation and safe structured logs."""

import json
import logging

import pytest
from pydantic import ValidationError

from orbit import Application, ApplicationConfig
from orbit.diagnostics import Diagnostics, DiagnosticSnapshot, JSONFormatter, RequestRecord
from orbit.diagnostics.inspection import inspect_composition
from orbit.plugins import Plugin, PluginMetadata
from orbit.runtime.context import bind_correlation_id, bind_span_id, bind_trace_id

REQUEST_ID = "00000000-0000-0000-0000-000000000001"


def test_bounded_history_preserves_cumulative_metrics_and_isolates_sinks(caplog):
    diagnostics = Diagnostics(history_size=1)
    captured = []

    class Broken:
        def record(self, request):
            raise RuntimeError("backend offline")

    class Healthy:
        def record(self, request):
            captured.append(request)

    broken, healthy = Broken(), Healthy()
    diagnostics.subscribe(broken)
    diagnostics.subscribe(healthy)
    diagnostics.record_request(
        RequestRecord(request_id=REQUEST_ID, method="GET", status=200, duration_seconds=0.01)
    )
    diagnostics.record_request(
        RequestRecord(
            request_id=REQUEST_ID, method="GET", status=200, duration_seconds=0.1, outcome="failed"
        )
    )
    diagnostics.record_request(
        RequestRecord(
            request_id=REQUEST_ID,
            method="GET",
            status=499,
            duration_seconds=0.2,
            outcome="cancelled",
        )
    )
    snapshot = diagnostics.collect(Application(ApplicationConfig(name="metrics")))
    assert snapshot.request_count == 3
    assert snapshot.error_count == 1
    assert snapshot.cancelled_count == 1
    assert len(snapshot.recent_requests) == 1
    assert snapshot.latency_buckets[1].count == 1
    assert len(captured) == 3
    assert "Telemetry sink failed" in caplog.text
    assert "backend offline" not in caplog.text
    assert diagnostics.unsubscribe(broken)
    assert not diagnostics.unsubscribe(broken)


def test_diagnostics_requires_a_callable_sink_record_method():
    with pytest.raises(TypeError, match="record"):
        Diagnostics().subscribe(object())  # type: ignore[arg-type]


def test_diagnostics_sink_capacity_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Telemetry subscription cannot grow an unbounded observer list."""
    monkeypatch.setattr("orbit.diagnostics.diagnostics._MAX_CORE_CAPACITY", 1)
    diagnostics = Diagnostics(history_size=1)

    class Sink:
        def record(self, request):
            return None

    diagnostics.subscribe(Sink())
    with pytest.raises(RuntimeError, match="capacity"):
        diagnostics.subscribe(Sink())


def test_diagnostics_rejects_invalid_records_before_mutating_cumulative_state() -> None:
    diagnostics = Diagnostics(history_size=2)
    with pytest.raises(TypeError, match="RequestRecord"):
        diagnostics.record_request(object())  # type: ignore[arg-type]

    huge = RequestRecord(
        request_id=REQUEST_ID,
        method="GET",
        status=200,
        duration_seconds=1e308,
    )
    diagnostics.record_request(huge)
    with pytest.raises(ValueError, match="finite"):
        diagnostics.record_request(huge)
    assert diagnostics._count == 1  # noqa: SLF001 - verify rejected input did not mutate state.
    assert len(diagnostics._history) == 1  # noqa: SLF001 - verify history atomicity.


@pytest.mark.parametrize("duration", [-1, float("inf"), float("nan")])
def test_invalid_latency_is_rejected(duration):
    with pytest.raises(ValidationError):
        RequestRecord(request_id=REQUEST_ID, method="GET", status=200, duration_seconds=duration)


@pytest.mark.parametrize("method", ["GET\n", "", "X" * 33, 1])
def test_diagnostic_methods_are_bounded_tokens(method):
    with pytest.raises(ValidationError):
        RequestRecord(request_id=REQUEST_ID, method=method, status=200, duration_seconds=0.1)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": True},
        {"status": "200"},
        {"duration_seconds": True},
        {"duration_seconds": "0.1"},
    ],
)
def test_request_diagnostic_numbers_are_strict(kwargs):
    with pytest.raises(ValidationError):
        RequestRecord(
            request_id=REQUEST_ID,
            method="GET",
            status=kwargs.get("status", 200),
            duration_seconds=kwargs.get("duration_seconds", 0.1),
        )


def test_json_logging_excludes_exception_message_and_unapproved_extras():
    error = ValueError("credential=private")
    record = logging.LogRecord(
        "test", logging.ERROR, "", 0, "operation failed", (), (ValueError, error, None)
    )
    record.authorization = "private"
    result = JSONFormatter().format(record)
    assert "private" not in result
    assert json.loads(result)["exception_type"] == "ValueError"
    assert json.loads(result)["request_id"] is None


def test_json_logging_includes_bound_trace_context():
    correlation = bind_correlation_id("corr-1")
    trace = bind_trace_id("a" * 32)
    span = bind_span_id("b" * 16)
    try:
        record = logging.LogRecord("test", logging.INFO, "", 0, "ok", (), None)
        payload = json.loads(JSONFormatter().format(record))
        assert payload["correlation_id"] == "corr-1"
        assert payload["trace_id"] == "a" * 32
        assert payload["span_id"] == "b" * 16
    finally:
        from orbit.runtime.context import reset_correlation_id, reset_span_id, reset_trace_id

        reset_span_id(span)
        reset_trace_id(trace)
        reset_correlation_id(correlation)


def test_negative_history_size_is_rejected():
    with pytest.raises(ValueError):
        Diagnostics(history_size=-1)
    with pytest.raises(ValueError):
        Diagnostics(history_size=True)
    with pytest.raises(ValueError, match="1,000,000"):
        Diagnostics(history_size=1_000_001)


def test_zero_history_disables_request_retention():
    diagnostics = Diagnostics(history_size=0)
    diagnostics.record_request(
        RequestRecord(
            request_id=REQUEST_ID,
            method="GET",
            status=200,
            duration_seconds=0.1,
            outcome="completed",
        )
    )
    assert len(diagnostics._history) == 0  # noqa: SLF001 - verify explicit zero retention.


def test_runtime_gauges_are_updated_from_current_application_state():
    application = Application(ApplicationConfig(name="runtime-metrics"))
    diagnostics = Diagnostics()
    diagnostics.update_runtime(application)
    values = {item.name: item.value for item in diagnostics.metrics.snapshots()}
    assert values["orbit_services_registered"] == 0
    assert values["orbit_tasks_registered"] == 0
    assert values["orbit_plugins_enabled"] == 0


def test_plugin_operator_surfaces_use_frozen_registration_identity() -> None:
    """Inspection and metric labels must agree after a plugin replaces live metadata."""

    class MutablePlugin(Plugin):
        metadata = PluginMetadata(name="orbit-observed", version="1.0.0")

    plugin = MutablePlugin()
    application = Application(ApplicationConfig(name="stable-plugin-surfaces"))
    application.register_plugin(plugin)
    plugin.metadata = plugin.metadata.model_copy(update={"name": "orbit-renamed"})

    composition = inspect_composition(application)
    assert composition.plugins[0].name == "orbit-observed"

    diagnostics = Diagnostics()
    diagnostics.update_runtime(application)
    plugin_labels = {
        item.labels["plugin"]
        for item in diagnostics.metrics.snapshots()
        if item.name == "orbit_plugin_health"
    }
    assert plugin_labels == {"orbit-observed"}


def test_provider_resolution_metrics_track_success_and_failure():
    diagnostics = Diagnostics()
    diagnostics.record_provider_resolution(True)
    diagnostics.record_provider_resolution(False)
    values = {item.name: item.value for item in diagnostics.metrics.snapshots()}
    assert values["orbit_container_resolutions_total"] == 2
    assert values["orbit_container_resolution_failures_total"] == 1


def test_request_response_metric_has_bounded_dimensions():
    diagnostics = Diagnostics()
    diagnostics.record_request(
        RequestRecord(request_id=REQUEST_ID, method="GET", status=200, duration_seconds=0.01)
    )
    snapshots = diagnostics.metrics.snapshots()
    response = next(item for item in snapshots if item.name == "orbit_http_responses_total")
    assert response.labels == {"method": "GET", "status": "200", "outcome": "completed"}
    assert response.value == 1
    diagnostics.record_request(
        RequestRecord(
            request_id=REQUEST_ID, method="CUSTOM-UNBOUNDED", status=500, duration_seconds=0.0
        )
    )
    methods = {
        item.labels["method"]
        for item in diagnostics.metrics.snapshots()
        if item.name == "orbit_http_responses_total"
    }
    assert methods == {"GET", "OTHER"}


def test_diagnostic_snapshots_freeze_nested_status_counts():
    diagnostics = Diagnostics()
    diagnostics.record_request(
        RequestRecord(request_id=REQUEST_ID, method="GET", status=200, duration_seconds=0.01)
    )
    snapshot = diagnostics.collect(Application(ApplicationConfig(name="immutable-diagnostics")))
    with pytest.raises(TypeError, match="immutable"):
        snapshot.status_counts[200] = 99


def test_diagnostic_snapshots_validate_strict_coherent_totals():
    """Snapshot totals cannot be coerced or disagree with their status population."""
    application = Diagnostics().collect(Application(ApplicationConfig(name="snapshot-contract")))

    with pytest.raises(ValidationError):
        DiagnosticSnapshot(
            application=application.application,
            request_count=1,
            status_counts={"200": 1},  # type: ignore[dict-item]
        )
    with pytest.raises(ValidationError):
        DiagnosticSnapshot(
            application=application.application,
            request_count=2,
            status_counts={200: 1},
        )
    with pytest.raises(ValidationError):
        DiagnosticSnapshot(
            application=application.application,
            request_count=0,
            error_count=1,
            status_counts={},
        )
    with pytest.raises(ValidationError):
        DiagnosticSnapshot(
            application=application.application,
            duration_seconds=float("nan"),
        )
