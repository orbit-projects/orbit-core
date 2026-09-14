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
"""Bounded concurrent health checks with failure isolation and current aggregation."""

import asyncio
import logging
from collections.abc import Mapping

from orbit.health.check import HealthCheck
from orbit.health.models import HealthReport, HealthStatus

_LOG = logging.getLogger(__name__)


class HealthService:
    """Aggregate current reports; an exception or timeout is unhealthy, never readiness."""

    async def check(self, checks: Mapping[str, HealthCheck], *, timeout: float = 2) -> HealthReport:
        """Check concurrently within individual deadlines; preserve caller cancellation."""

        async def run(check: HealthCheck) -> HealthReport:
            try:
                async with asyncio.timeout(timeout):
                    report = await check.health()
                    if not isinstance(report, HealthReport):
                        raise TypeError("Health checks must return HealthReport.")
                    return report
            except Exception:
                _LOG.exception("Component health check failed")
                return HealthReport(status=HealthStatus.UNHEALTHY, message="Health check failed.")

        names = tuple(checks)
        reports = await asyncio.gather(*(run(checks[name]) for name in names))
        statuses = {report.status for report in reports}
        status = HealthStatus.HEALTHY
        if HealthStatus.UNHEALTHY in statuses:
            status = HealthStatus.UNHEALTHY
        elif statuses & {HealthStatus.DEGRADED, HealthStatus.UNKNOWN}:
            status = HealthStatus.DEGRADED
        return HealthReport(
            status=status,
            details={
                name: report.model_dump(mode="json")
                for name, report in zip(names, reports, strict=True)
            },
        )


__all__ = ["HealthService"]
