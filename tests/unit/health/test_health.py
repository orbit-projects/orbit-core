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
from collections.abc import Iterator, Mapping
from datetime import datetime

import pytest

from orbit.health import HealthReport, HealthService, HealthStatus


class _MisreportingChecks(Mapping[str, object]):
    """Health-check mapping that reports no entries but yields more than the registry limit."""

    def __init__(self, count: int, check: object) -> None:
        self._count = count
        self._check = check

    def __getitem__(self, key: str) -> object:
        index = int(key)
        if 0 <= index < self._count:
            return self._check
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(str(index) for index in range(self._count))

    def __len__(self) -> int:
        return 0


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


async def test_health_timeout_detaches_cancellation_resistant_check_without_blocking() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class StubbornCheck:
        async def health(self):
            started.set()
            if release.is_set():
                return HealthReport(status=HealthStatus.HEALTHY)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
                return HealthReport(status=HealthStatus.HEALTHY)

    service = HealthService()
    check = StubbornCheck()
    report = await asyncio.wait_for(service.check({"database": check}, timeout=0.01), timeout=0.1)
    assert report.status is HealthStatus.UNHEALTHY
    assert report.details["database"]["message"] == "Health check timed out."
    assert started.is_set()

    repeated = await service.check({"database": check}, timeout=0.01)
    assert repeated.status is HealthStatus.UNHEALTHY
    assert repeated.details["database"]["message"] == "Health check is still cancelling."

    release.set()
    for _ in range(20):
        await asyncio.sleep(0)
        if not service._detached:  # noqa: SLF001 - wait for the test-owned detached check.
            break
    assert not service._detached  # noqa: SLF001 - verify detached cleanup completed.
    recovered = await service.check({"database": check}, timeout=0.1)
    assert recovered.status is HealthStatus.HEALTHY


async def test_health_retires_completed_failure_when_aggregate_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation after provider completion must consume the completed failure."""
    import orbit.health.health as health_module

    original_wait = asyncio.wait

    async def wait_then_cancel(tasks, *, timeout):
        done, pending = await original_wait(tasks, timeout=timeout)
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        await asyncio.sleep(0)
        return done, pending

    monkeypatch.setattr(health_module.asyncio, "wait", wait_then_cancel)

    class FailingCheck:
        async def health(self):
            raise RuntimeError("private provider failure")

    service = HealthService()
    with pytest.raises(asyncio.CancelledError):
        await service.check({"database": FailingCheck()}, timeout=0.1)
    assert not service._running  # noqa: SLF001 - verify completed failure retirement.


async def test_health_detached_tasks_are_isolated_per_check_name() -> None:
    release = asyncio.Event()

    class StubbornCheck:
        async def health(self):
            while not release.is_set():
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    continue
            return HealthReport(status=HealthStatus.HEALTHY)

    class HealthyCheck:
        async def health(self):
            return HealthReport(status=HealthStatus.HEALTHY)

    service = HealthService()
    first = await service.check(
        {"database": StubbornCheck(), "cache": HealthyCheck()}, timeout=0.01
    )
    assert first.status is HealthStatus.UNHEALTHY
    assert first.details["database"]["message"] == "Health check timed out."
    assert first.details["cache"]["status"] == "healthy"

    second = await service.check(
        {"database": StubbornCheck(), "cache": HealthyCheck()}, timeout=0.01
    )
    assert second.details["database"]["message"] == "Health check is still cancelling."
    assert second.details["cache"]["status"] == "healthy"

    release.set()
    for _ in range(20):
        await asyncio.sleep(0)
        if not service._detached:  # noqa: SLF001 - wait for test-owned detached work.
            break
    assert not service._detached  # noqa: SLF001 - verify per-check retirement.


async def test_concurrent_health_checks_do_not_duplicate_one_provider_call() -> None:
    """Concurrent probes share one provider task and receive its completed report."""
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class StubbornCheck:
        async def health(self):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return HealthReport(status=HealthStatus.HEALTHY)

    service = HealthService()
    first = asyncio.create_task(service.check({"database": StubbornCheck()}, timeout=0.1))
    await started.wait()
    second = asyncio.create_task(service.check({"database": StubbornCheck()}, timeout=0.1))
    await asyncio.sleep(0)
    release.set()

    first_report, second_report = await asyncio.gather(first, second)
    assert calls == 1
    assert first_report.details["database"]["status"] == "healthy"
    assert second_report.details["database"]["status"] == "healthy"


async def test_completed_shared_health_task_is_reused_before_owner_retires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed provider task cannot be replaced during the first waiter's completion gap."""
    import orbit.health.health as health_module

    original_wait = asyncio.wait
    owner_paused = asyncio.Event()
    allow_owner = asyncio.Event()
    first_wait = True

    async def wait_with_completion_gap(tasks, *, timeout):
        nonlocal first_wait
        done, pending = await original_wait(tasks, timeout=timeout)
        if first_wait:
            first_wait = False
            owner_paused.set()
            await allow_owner.wait()
        return done, pending

    monkeypatch.setattr(health_module.asyncio, "wait", wait_with_completion_gap)
    calls = 0

    class QuickCheck:
        async def health(self):
            nonlocal calls
            calls += 1
            return HealthReport(status=HealthStatus.HEALTHY)

    service = HealthService()
    first = asyncio.create_task(service.check({"database": QuickCheck()}, timeout=0.1))
    await owner_paused.wait()
    second = asyncio.create_task(service.check({"database": QuickCheck()}, timeout=0.1))
    await asyncio.sleep(0)
    allow_owner.set()

    first_report, second_report = await asyncio.gather(first, second)
    assert calls == 1
    assert first_report.details["database"]["status"] == "healthy"
    assert second_report.details["database"]["status"] == "healthy"


async def test_close_transfers_cancellation_resistant_running_checks_to_detached_ownership() -> (
    None
):
    """Bounded shutdown must not retain a late provider task in the active check registry."""
    started = asyncio.Event()
    release = asyncio.Event()

    class StubbornCheck:
        async def health(self) -> HealthReport:
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    # Model a third-party provider that suppresses every cancellation request.
                    continue
            raise RuntimeError("late provider failure") from None

    service = HealthService()
    probe = asyncio.create_task(service.check({"database": StubbornCheck()}, timeout=1))
    await asyncio.wait_for(started.wait(), timeout=0.2)

    await service.close(timeout=0.01)

    assert "database" not in service._running  # noqa: SLF001 - verify shutdown ownership.
    assert "database" in service._detached  # noqa: SLF001 - verify late-task retirement.

    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe
    release.set()

    async def wait_for_retirement() -> None:
        while service._detached:  # noqa: SLF001 - wait for the ownership callback.
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_retirement(), timeout=0.2)


async def test_health_close_bounds_shutdown_for_cancellation_resistant_checks() -> None:
    release = asyncio.Event()

    class StubbornCheck:
        async def health(self):
            while not release.is_set():
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    continue
            return HealthReport(status=HealthStatus.HEALTHY)

    service = HealthService()
    await service.check({"database": StubbornCheck()}, timeout=0.01)
    await asyncio.wait_for(service.close(timeout=0.01), timeout=0.1)
    assert service._detached  # noqa: SLF001 - verify bounded shutdown retains late work.

    release.set()
    for _ in range(20):
        await asyncio.sleep(0)
        if not service._detached:  # noqa: SLF001 - wait for test-owned cleanup.
            break
    assert not service._detached  # noqa: SLF001 - verify eventual retirement.


async def test_cancelled_health_close_finishes_detached_cleanup_before_propagating() -> None:
    """A cancelled health owner cannot abandon the service's detached checks."""
    release = asyncio.Event()

    class StubbornCheck:
        async def health(self):
            while not release.is_set():
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    continue
            return HealthReport(status=HealthStatus.HEALTHY)

    service = HealthService()
    await service.check({"database": StubbornCheck()}, timeout=0.01)
    closing = asyncio.create_task(service.close(timeout=0.1))
    await asyncio.sleep(0)
    closing.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    await service.close(timeout=0.1)
    assert not service._detached  # noqa: SLF001 - verify the shared close task completed.


async def test_health_close_rejects_invalid_timeout() -> None:
    service = HealthService()
    with pytest.raises(ValueError, match="finite and positive"):
        await service.close(timeout=0)
    with pytest.raises(ValueError, match="finite and positive"):
        await service.close(timeout=float("inf"))


async def test_health_timeout_must_be_finite_and_positive():
    with pytest.raises(ValueError, match="finite and positive"):
        await HealthService().check({}, timeout=0)
    with pytest.raises(ValueError, match="finite and positive"):
        await HealthService().check({}, timeout=float("inf"))
    with pytest.raises(ValueError, match="finite and positive"):
        await HealthService().check({}, timeout="1")  # type: ignore[arg-type]


async def test_health_check_registry_validates_bounded_contracts():
    class Check:
        async def health(self):
            return HealthReport(status=HealthStatus.HEALTHY)

    with pytest.raises(TypeError, match="mapping"):
        await HealthService().check([], timeout=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="callable health"):
        await HealthService().check({"broken": object()}, timeout=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="bounded printable"):
        await HealthService().check({"bad\nname": Check()}, timeout=1)
    with pytest.raises(ValueError, match="bounded printable"):
        await HealthService().check({"x" * 129: Check()}, timeout=1)
    with pytest.raises(ValueError, match="cannot exceed"):
        await HealthService().check({str(index): Check() for index in range(1001)}, timeout=1)
    with pytest.raises(ValueError, match="cannot exceed"):
        await HealthService().check(_MisreportingChecks(1001, Check()), timeout=1)  # type: ignore[arg-type]


def test_health_report_rejects_naive_timestamp():
    with pytest.raises(ValueError, match="timezone"):
        HealthReport(checked_at=datetime(2026, 1, 1))


@pytest.mark.parametrize("message", ["bad\nmessage", "x" * 1025])
def test_health_report_rejects_unsafe_messages(message: str) -> None:
    with pytest.raises(ValueError):
        HealthReport(message=message)


@pytest.mark.parametrize("details", [{"bad\nkey": True}, {"x" * 256: True}])
def test_health_report_rejects_unsafe_detail_keys(details: dict[str, bool]) -> None:
    with pytest.raises(ValueError, match="detail keys"):
        HealthReport(details=details)


def test_health_report_bounds_detail_cardinality() -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        HealthReport(details={str(index): True for index in range(2_049)})

    with pytest.raises(ValueError, match="cannot exceed"):
        HealthReport(details=_MisreportingChecks(2_049, True))
