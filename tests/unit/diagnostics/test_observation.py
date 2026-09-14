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
from orbit.diagnostics import Diagnostics, JSONFormatter, RequestRecord


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
        RequestRecord(request_id="a", method="GET", status=200, duration_seconds=0.01)
    )
    diagnostics.record_request(
        RequestRecord(
            request_id="b", method="GET", status=200, duration_seconds=0.1, outcome="failed"
        )
    )
    diagnostics.record_request(
        RequestRecord(
            request_id="c", method="GET", status=499, duration_seconds=0.2, outcome="cancelled"
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
    assert diagnostics.unsubscribe(broken)
    assert not diagnostics.unsubscribe(broken)


@pytest.mark.parametrize("duration", [-1, float("inf"), float("nan")])
def test_invalid_latency_is_rejected(duration):
    with pytest.raises(ValidationError):
        RequestRecord(request_id="id", method="GET", status=200, duration_seconds=duration)


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


def test_negative_history_size_is_rejected():
    with pytest.raises(ValueError):
        Diagnostics(history_size=-1)
