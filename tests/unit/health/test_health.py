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
"""Health check failure isolation, timeouts and degradation."""

import asyncio

import pytest

from orbit.health import HealthReport, HealthService, HealthStatus


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("unknown", "degraded"),
        ("degraded", "degraded"),
        ("failure", "unhealthy"),
        ("timeout", "unhealthy"),
        ("healthy", "healthy"),
    ],
)
async def test_reports_fail_closed(kind, expected):
    class Check:
        async def health(self):
            if kind == "failure":
                raise ValueError("private")
            if kind == "timeout":
                await asyncio.Event().wait()
            return HealthReport(status=HealthStatus(kind))

    report = await HealthService().check({"check": Check()}, timeout=0.01)
    assert report.status.value == expected
    assert "private" not in report.model_dump_json()
