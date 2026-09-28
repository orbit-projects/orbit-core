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
"""Bounded request diagnostics and failure-isolated telemetry hooks.

Telemetry sinks are application-provided code. Core therefore isolates sink failures and records
only a constant diagnostic message; exception text and tracebacks can contain provider payloads
or credentials and must not enter the framework's operator log by default.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING

from orbit._limits import _MAX_CORE_CAPACITY, is_finite_number
from orbit.application.models import ApplicationSummary
from orbit.diagnostics.metrics import MetricsRegistry
from orbit.diagnostics.models import DiagnosticSnapshot, LatencyBucket, RequestRecord, TelemetrySink
from orbit.health import HealthStatus

if TYPE_CHECKING:
    from orbit.application import Application
_LOG = logging.getLogger(__name__)
_METRIC_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}


class Diagnostics:
    """Collect Core state and payload-free telemetry on the application's event loop."""

    def __init__(self, *, history_size: int = 100) -> None:
        if (
            isinstance(history_size, bool)
            or not isinstance(history_size, int)
            or not 0 <= history_size <= _MAX_CORE_CAPACITY
        ):
            raise ValueError("history_size must be between 0 and 1,000,000.")
        self._history: deque[RequestRecord] = deque(maxlen=history_size)
        self._sinks: list[TelemetrySink] = []
        self._count = 0
        self._errors = 0
        self._duration = 0.0
        self._buckets = dict.fromkeys((0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 5, 30), 0)
        self._statuses: dict[int, int] = {}
        self._cancelled = 0
        self._disconnected = 0
        self.metrics = MetricsRegistry()
        self._request_counter = self.metrics.counter("orbit_http_requests_total")
        self._response_counter = self.metrics.counter(
            "orbit_http_responses_total", labels=("method", "status", "outcome")
        )
        self._request_duration = self.metrics.histogram("orbit_http_request_duration_seconds")
        self._services_gauge = self.metrics.gauge("orbit_services_registered")
        self._tasks_gauge = self.metrics.gauge("orbit_tasks_registered")
        self._failed_tasks_gauge = self.metrics.gauge("orbit_tasks_failed")
        self._plugins_gauge = self.metrics.gauge("orbit_plugins_enabled")
        self._service_health = self.metrics.gauge(
            "orbit_service_health", labels=("service", "status")
        )
        self._plugin_health = self.metrics.gauge("orbit_plugin_health", labels=("plugin", "status"))
        self._events_gauge = self.metrics.gauge("orbit_events_published")
        self._event_failures_gauge = self.metrics.gauge("orbit_event_delivery_failures")
        self._event_deduplicated_gauge = self.metrics.gauge("orbit_events_deduplicated")
        self._container_resolutions = self.metrics.counter("orbit_container_resolutions_total")
        self._container_failures = self.metrics.counter("orbit_container_resolution_failures_total")

    def subscribe(self, sink: TelemetrySink) -> None:
        """Install a nonblocking telemetry backend; observer failures are logged and isolated."""
        if not callable(getattr(sink, "record", None)):
            raise TypeError("Telemetry sinks must provide a callable record method.")
        if len(self._sinks) >= _MAX_CORE_CAPACITY:
            raise RuntimeError("Telemetry sink capacity reached.")
        self._sinks.append(sink)

    def unsubscribe(self, sink: TelemetrySink) -> bool:
        """Remove an observer by identity; return whether it was subscribed."""
        for index, registered in enumerate(self._sinks):
            if registered is sink:
                del self._sinks[index]
                return True
        return False

    def record_request(self, record: RequestRecord) -> None:
        """Update cumulative metrics and retain only bounded diagnostic history."""
        if not isinstance(record, RequestRecord):
            raise TypeError("Diagnostics require a RequestRecord instance.")
        updated_duration = self._duration + record.duration_seconds
        if not is_finite_number(updated_duration):
            raise ValueError("Cumulative request duration must remain finite.")
        self._history.append(record)
        self._request_counter.inc()
        self._response_counter.inc(
            method=record.method.upper() if record.method.upper() in _METRIC_METHODS else "OTHER",
            status=str(record.status),
            outcome=record.outcome,
        )
        self._request_duration.observe(record.duration_seconds)
        self._count += 1
        self._errors += int(record.status >= 500 or record.outcome == "failed")
        self._duration = updated_duration
        self._cancelled += int(record.outcome == "cancelled")
        self._disconnected += int(record.outcome == "disconnected")
        self._statuses[record.status] = self._statuses.get(record.status, 0) + 1
        for bound in self._buckets:
            self._buckets[bound] += int(record.duration_seconds <= bound)
        for sink in tuple(self._sinks):
            try:
                sink.record(record)
            except Exception:
                # Do not attach the exception: a sink may include credentials or provider data
                # in its message. The host can instrument sink health separately if it needs
                # backend-specific failure details.
                _LOG.error("Telemetry sink failed; observer isolated.")

    def collect(self, application: Application) -> DiagnosticSnapshot:
        """Capture current Core state and cumulative request metrics."""
        self.update_runtime(application)
        return DiagnosticSnapshot(
            application=ApplicationSummary(
                state=application.state.application,
                service_names=tuple(d.name for d in application.services.descriptors),
            ),
            request_count=self._count,
            error_count=self._errors,
            duration_seconds=self._duration,
            recent_requests=tuple(self._history),
            latency_buckets=tuple(
                LatencyBucket(upper_bound=bound, count=count)
                for bound, count in self._buckets.items()
            ),
            status_counts=dict(self._statuses),
            cancelled_count=self._cancelled,
            disconnected_count=self._disconnected,
        )

    def export(self, application: Application, *, indent: int | None = None) -> str:
        """Serialize one detached diagnostics snapshot for incident export or transport."""
        if indent is not None and (
            isinstance(indent, bool) or not isinstance(indent, int) or indent < 0 or indent > 8
        ):
            raise ValueError("indent must be between 0 and 8.")
        return self.collect(application).model_dump_json(indent=indent)

    def update_runtime(self, application: Application) -> None:
        """Publish bounded runtime gauges without retaining application or component objects."""
        self._services_gauge.set(len(application.services.services))
        self._tasks_gauge.set(len(application.tasks.infos))
        self._failed_tasks_gauge.set(
            sum(task.state.value == "failed" for task in application.tasks.infos)
        )
        self._plugins_gauge.set(len(application.plugins.enabled_names))
        statuses = tuple(status.value for status in HealthStatus)
        for service in application.state.application.services:
            for status in statuses:
                self._service_health.set(
                    1 if service.health.value == status else 0,
                    service=service.name,
                    status=status,
                )
        for plugin in application.plugins.plugins:
            # Use the registry snapshot so a plugin cannot rename its metric series by replacing
            # its live metadata after composition.
            plugin_name = application.plugins.snapshot_for(plugin).name
            status = HealthStatus.UNKNOWN.value
            if application.health_history:
                detail = application.health_history[-1].details.get(f"plugin:{plugin_name}", {})
                if isinstance(detail, dict) and isinstance(detail.get("status"), str):
                    status = detail["status"]
            for candidate in statuses:
                self._plugin_health.set(
                    1 if status == candidate else 0,
                    plugin=plugin_name,
                    status=candidate,
                )
        published, failures, deduplicated = application.events.stats
        self._events_gauge.set(published)
        self._event_failures_gauge.set(failures)
        self._event_deduplicated_gauge.set(deduplicated)

    def record_provider_resolution(self, success: bool) -> None:
        """Record one provider construction outcome without retaining its resolved value."""
        self._container_resolutions.inc()
        if not success:
            self._container_failures.inc()


__all__ = ["Diagnostics"]
