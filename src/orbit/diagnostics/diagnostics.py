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
"""Bounded request diagnostics and failure-isolated telemetry hooks."""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING

from orbit.application.models import ApplicationSummary
from orbit.diagnostics.models import DiagnosticSnapshot, RequestRecord, TelemetrySink

if TYPE_CHECKING:
    from orbit.application import Application
_LOG = logging.getLogger(__name__)


class Diagnostics:
    """Collect Core state and payload-free telemetry on the application's event loop."""

    def __init__(self, *, history_size: int = 100) -> None:
        self._history: deque[RequestRecord] = deque(maxlen=history_size)
        self._sinks: list[TelemetrySink] = []
        self._count = 0
        self._errors = 0
        self._duration = 0.0

    def subscribe(self, sink: TelemetrySink) -> None:
        """Install a nonblocking telemetry backend; observer failures are logged and isolated."""
        self._sinks.append(sink)

    def record_request(self, record: RequestRecord) -> None:
        """Update cumulative metrics and retain only bounded diagnostic history."""
        self._history.append(record)
        self._count += 1
        self._errors += int(record.status >= 500)
        self._duration += record.duration_seconds
        for sink in tuple(self._sinks):
            try:
                sink.record(record)
            except Exception:
                _LOG.exception("Telemetry sink failed")

    def collect(self, application: Application) -> DiagnosticSnapshot:
        """Capture current Core state and cumulative request metrics."""
        return DiagnosticSnapshot(
            application=ApplicationSummary(
                state=application.state.application,
                service_names=tuple(d.name for d in application.services.descriptors),
            ),
            request_count=self._count,
            error_count=self._errors,
            duration_seconds=self._duration,
            recent_requests=tuple(self._history),
        )


__all__ = ["Diagnostics"]
