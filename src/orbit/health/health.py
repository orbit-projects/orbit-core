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

from orbit._limits import is_finite_number
from orbit.health.check import HealthCheck
from orbit.health.models import HealthReport, HealthStatus

_LOG = logging.getLogger(__name__)
_MAX_CHECKS = 1_000
_MAX_CHECK_NAME_LENGTH = 128


class HealthService:
    """Aggregate current reports; an exception or timeout is unhealthy, never readiness.

    A provider health coroutine that suppresses cancellation is detached after its deadline so it
    cannot hold readiness or an HTTP request open. One detached check is retained per name until
    it exits; repeated probes fail closed instead of creating an unbounded set of orphan tasks.
    """

    def __init__(self) -> None:
        self._detached: dict[str, asyncio.Task[HealthReport]] = {}
        self._close_task: asyncio.Task[None] | None = None

    def cancel_pending(self) -> None:
        """Request cancellation of health checks that outlived an earlier deadline."""
        for task in tuple(self._detached.values()):
            if not task.done():
                task.cancel()

    async def close(self, timeout: float = 1.0) -> None:
        """Cancel detached checks and wait up to ``timeout`` for cooperative cleanup.

        Health checks are normally short-lived, but a provider may suppress cancellation. The
        application therefore owns a finite shutdown wait rather than awaiting such a task
        forever. Tasks that remain pending stay tracked and are retired by their existing done
        callbacks when the provider eventually cooperates.
        """
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not is_finite_number(timeout)
            or timeout <= 0
        ):
            raise ValueError("Health cleanup timeout must be finite and positive.")
        if self._close_task is None or (self._close_task.done() and self._detached):
            self._close_task = asyncio.create_task(self._close(timeout))
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self, timeout: float) -> None:
        """Perform the shared bounded health-check cleanup operation."""
        self.cancel_pending()
        tasks = tuple(self._detached.values())
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)
        for name, task in tuple(self._detached.items()):
            if task.done():
                self._retire(name, task)

    def _retire(self, name: str, task: asyncio.Task[HealthReport]) -> None:
        """Forget one detached task and consume any late exception without warnings."""
        if self._detached.get(name) is task:
            del self._detached[name]
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            return

    async def check(self, checks: Mapping[str, HealthCheck], *, timeout: float = 2) -> HealthReport:
        """Run checks concurrently under deadlines and aggregate their statuses.

        A failed or timed-out check becomes unhealthy while other checks still complete. Caller
        cancellation propagates, and check exceptions are logged without escaping the aggregate.
        Check names and cardinality are bounded before any tasks are created, while plugins define
        the checks and provider-specific readiness interpretation.
        """
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not is_finite_number(timeout)
            or timeout <= 0
        ):
            raise ValueError("Health check timeout must be finite and positive.")
        if not isinstance(checks, Mapping):
            raise TypeError("Health checks must be supplied as a mapping.")
        if len(checks) > _MAX_CHECKS:
            raise ValueError(f"Health checks cannot exceed {_MAX_CHECKS} entries.")
        names = tuple(checks)
        for name in names:
            if (
                not isinstance(name, str)
                or not 1 <= len(name) <= _MAX_CHECK_NAME_LENGTH
                or any(ord(character) < 32 or ord(character) == 127 for character in name)
            ):
                raise ValueError("Health check names must be bounded printable strings.")
            if not callable(getattr(checks[name], "health", None)):
                raise TypeError(f"Health check {name!r} must provide a callable health method.")

        async def run(name: str, check: HealthCheck) -> HealthReport:
            """Execute one named check under its deadline and isolate provider failures."""
            existing = self._detached.get(name)
            if existing is not None:
                if existing.done():
                    self._retire(name, existing)
                else:
                    return HealthReport(
                        status=HealthStatus.UNHEALTHY,
                        message="Health check is still cancelling.",
                    )
            task: asyncio.Task[HealthReport] | None = None
            try:
                task = asyncio.create_task(check.health())
                done, _ = await asyncio.wait({task}, timeout=timeout)
                if not done:
                    task.cancel()
                    if task.done():
                        self._retire(name, task)
                    else:
                        self._detached[name] = task
                        task.add_done_callback(lambda finished: self._retire(name, finished))
                    return HealthReport(
                        status=HealthStatus.UNHEALTHY,
                        message="Health check timed out.",
                    )
                report = task.result()
                if not isinstance(report, HealthReport):
                    raise TypeError("Health checks must return HealthReport.")
                return report
            except asyncio.CancelledError:
                if task is not None and not task.done():
                    task.cancel()
                    self._detached[name] = task
                    task.add_done_callback(lambda finished: self._retire(name, finished))
                raise
            except Exception:
                _LOG.exception("Component health check failed")
                return HealthReport(status=HealthStatus.UNHEALTHY, message="Health check failed.")

        reports = await asyncio.gather(*(run(name, checks[name]) for name in names))
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
