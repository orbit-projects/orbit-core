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
"""Verify task supervision, bounded cancellation, and restart semantics."""

import asyncio

import pytest

from orbit.runtime import RestartPolicy, TaskFailure, TaskInfo, TaskState, TaskSupervisor


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TaskFailure("bad name", 1, "ValueError", 0),
        lambda: TaskFailure("worker", 0, "ValueError", 0),
        lambda: TaskFailure("worker", 1, "bad type", 0),
        lambda: TaskFailure("worker", 1, "ValueError", float("nan")),
        lambda: TaskFailure("worker", 1, "ValueError", -1),
    ],
)
def test_task_failure_snapshots_reject_invalid_public_values(factory) -> None:
    """Failure metadata keeps names, attempts, exception types and timing bounded."""
    with pytest.raises(ValueError):
        factory()


def test_task_info_snapshot_rejects_invalid_state_contract() -> None:
    failure = TaskFailure("worker", 1, "ValueError", 0)
    with pytest.raises(TypeError, match="Task state"):
        TaskInfo("worker", "running", 1, failure)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="attempts"):
        TaskInfo("worker", TaskState.RUNNING, -1, failure)
    with pytest.raises(TypeError, match="last_failure"):
        TaskInfo("worker", TaskState.RUNNING, 1, object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_supervisor_stops_running_task() -> None:
    stopped = asyncio.Event()

    async def worker() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    supervisor = TaskSupervisor(shutdown_timeout=1)
    supervisor.register("worker", worker)
    await supervisor.start()
    await asyncio.sleep(0)
    await supervisor.stop()

    assert stopped.is_set()
    assert supervisor.infos[0].state is TaskState.STOPPED


def test_supervisor_rejects_non_finite_timeouts() -> None:
    with pytest.raises(ValueError):
        TaskSupervisor(shutdown_timeout=float("nan"))
    with pytest.raises(ValueError):
        TaskSupervisor(observer_timeout=float("inf"))
    supervisor = TaskSupervisor()
    with pytest.raises(ValueError):
        supervisor.register("worker", lambda: None, restart_delay=float("inf"))


def test_supervisor_task_registration_capacity_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Background task specifications cannot grow beyond Core's shared capacity."""
    monkeypatch.setattr("orbit.runtime.tasks._MAX_CORE_CAPACITY", 1)
    supervisor = TaskSupervisor()
    supervisor.register("first", lambda: asyncio.sleep(0))
    with pytest.raises(RuntimeError, match="capacity"):
        supervisor.register("second", lambda: asyncio.sleep(0))


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TaskSupervisor(shutdown_timeout=True),
        lambda: TaskSupervisor(shutdown_timeout="1"),
        lambda: TaskSupervisor(history_size=True),
        lambda: TaskSupervisor(history_size="1"),
        lambda: TaskSupervisor(history_size=1_000_001),
        lambda: TaskSupervisor().register("worker", lambda: None, max_restarts=True),
        lambda: TaskSupervisor().register("worker", lambda: None, max_restarts="1"),
        lambda: TaskSupervisor().register("worker", lambda: None, max_restarts=1_000_001),
        lambda: TaskSupervisor().register("worker", lambda: None, restart_delay=True),
        lambda: TaskSupervisor().register("worker", lambda: None, restart_delay="1"),
    ],
)
def test_supervisor_rejects_boolean_numeric_limits(factory) -> None:
    with pytest.raises(ValueError):
        factory()


@pytest.mark.asyncio
async def test_supervisor_restarts_with_bounded_budget() -> None:
    attempts = 0

    async def worker() -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("transient")

    supervisor = TaskSupervisor(shutdown_timeout=1)
    supervisor.register(
        "worker", worker, policy=RestartPolicy.ON_FAILURE, max_restarts=2, restart_delay=0
    )
    await supervisor.start()
    await asyncio.sleep(0.02)
    await supervisor.stop()

    info = supervisor.infos[0]
    assert attempts == 3
    assert info.state is TaskState.STOPPED
    assert len(supervisor.history) == 2


@pytest.mark.asyncio
async def test_supervisor_zero_history_disables_failure_retention() -> None:
    """A zero retention policy must not accidentally grow the internal failure list."""

    async def worker() -> None:
        raise RuntimeError("not retained")

    supervisor = TaskSupervisor(history_size=0)
    supervisor.register("worker", worker)
    await supervisor.start()

    async def wait_for_failed_state() -> None:
        while supervisor.infos[0].state is not TaskState.FAILED:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_failed_state(), timeout=0.2)
    assert supervisor.history == ()
    assert supervisor.infos[0].last_failure is not None
    await supervisor.stop()


@pytest.mark.asyncio
async def test_supervisor_supports_explicit_restart_and_preserves_history() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    starts = 0

    async def worker() -> None:
        nonlocal starts
        starts += 1
        started.set()
        await release.wait()

    supervisor = TaskSupervisor(shutdown_timeout=1)
    supervisor.register("worker", worker)
    await supervisor.start()
    await started.wait()
    await supervisor.restart("worker")
    started.clear()
    # The replacement is observable as a new invocation before shutdown.
    await asyncio.sleep(0)
    assert starts == 2
    assert supervisor.infos[0].state is TaskState.RUNNING
    release.set()
    await supervisor.stop()


@pytest.mark.asyncio
async def test_explicit_restart_does_not_report_intentional_cancellation_as_failure() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    observed = []

    async def observer(failure):
        observed.append(failure)

    async def worker() -> None:
        started.set()
        await release.wait()

    supervisor = TaskSupervisor(observer=observer, shutdown_timeout=1)
    supervisor.register("worker", worker)
    await supervisor.start()
    await started.wait()
    await supervisor.restart("worker")

    assert supervisor.history == ()
    assert observed == []
    release.set()
    await supervisor.stop()


@pytest.mark.asyncio
async def test_restart_propagates_operator_cancellation_without_replacement() -> None:
    cancellation_seen = asyncio.Event()
    release = asyncio.Event()

    async def worker() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancellation_seen.set()
            await release.wait()

    supervisor = TaskSupervisor(shutdown_timeout=1)
    supervisor.register("worker", worker)
    await supervisor.start()
    await asyncio.sleep(0)
    current = supervisor._tasks["worker"]  # noqa: SLF001 - inspect test-owned task identity.
    restart = asyncio.create_task(supervisor.restart("worker"))
    await asyncio.wait_for(cancellation_seen.wait(), timeout=0.2)
    restart.cancel()
    with pytest.raises(asyncio.CancelledError):
        await restart

    assert supervisor._tasks["worker"] is current  # noqa: SLF001
    release.set()
    await asyncio.wait_for(current, timeout=0.2)
    await supervisor.stop()


@pytest.mark.asyncio
async def test_supervisor_serializes_restart_and_stop() -> None:
    started = asyncio.Event()

    async def worker() -> None:
        started.set()
        await asyncio.Event().wait()

    supervisor = TaskSupervisor(shutdown_timeout=1)
    supervisor.register("worker", worker)
    await supervisor.start()
    await started.wait()
    restart = asyncio.create_task(supervisor.restart("worker"))
    stop = asyncio.create_task(supervisor.stop())
    results = await asyncio.gather(restart, stop, return_exceptions=True)

    assert all(isinstance(result, (type(None), RuntimeError)) for result in results)
    assert supervisor.infos[0].state is TaskState.STOPPED


def test_supervisor_validates_restart_policy() -> None:
    supervisor = TaskSupervisor()

    async def worker() -> None:
        return

    with pytest.raises(ValueError, match="positive"):
        supervisor.register("worker", worker, policy=RestartPolicy.ON_FAILURE)
    with pytest.raises(TypeError, match="RestartPolicy"):
        supervisor.register("string-policy", worker, policy="never")  # type: ignore[arg-type]
    for name in ("bad\nname", "BadName", "with space", "task/name", "x" * 128):
        with pytest.raises(ValueError, match="lowercase"):
            supervisor.register(name, worker)


@pytest.mark.asyncio
async def test_supervisor_rejects_invalid_restart_names() -> None:
    supervisor = TaskSupervisor()
    with pytest.raises(TypeError, match="Task names"):
        await supervisor.restart([])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="lowercase"):
        await supervisor.restart("task/name")


@pytest.mark.asyncio
async def test_supervisor_records_failure_and_observer_errors_are_isolated() -> None:
    observed = []
    observer_done = asyncio.Event()

    async def observer(failure):
        observed.append(failure)
        try:
            raise RuntimeError("observer failed")
        finally:
            observer_done.set()

    async def worker() -> None:
        raise ValueError("bad")

    supervisor = TaskSupervisor(observer=observer)
    supervisor.register("worker", worker)
    await supervisor.start()
    await asyncio.wait_for(observer_done.wait(), timeout=0.2)

    async def wait_for_failed_state() -> None:
        while supervisor.infos[0].state is not TaskState.FAILED:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_failed_state(), timeout=0.2)
    assert supervisor.infos[0].state is TaskState.FAILED
    assert observed and observed[0].error_type == "ValueError"
    await supervisor.stop()


@pytest.mark.asyncio
async def test_supervisor_bounds_async_observer_before_restart() -> None:
    attempts = 0
    observer_started = asyncio.Event()
    second_attempt = asyncio.Event()

    async def observer(_: object) -> None:
        observer_started.set()
        await asyncio.Event().wait()

    async def worker() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            second_attempt.set()
        raise RuntimeError("transient")

    supervisor = TaskSupervisor(observer=observer, observer_timeout=0.01)
    supervisor.register("worker", worker, policy=RestartPolicy.ON_FAILURE, max_restarts=1)
    await supervisor.start()
    await asyncio.wait_for(observer_started.wait(), timeout=0.2)
    await asyncio.wait_for(second_attempt.wait(), timeout=0.2)

    async def wait_for_failed_state() -> None:
        while supervisor.infos[0].state is not TaskState.FAILED:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_failed_state(), timeout=0.2)

    assert attempts == 2
    assert supervisor.infos[0].state is TaskState.FAILED
    await supervisor.stop()


@pytest.mark.asyncio
async def test_supervisor_detaches_cancellation_suppressing_observer() -> None:
    observer_started = asyncio.Event()
    observer_released = asyncio.Event()
    observer_done = asyncio.Event()

    async def observer(_: object) -> None:
        observer_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await observer_released.wait()
            observer_done.set()

    async def worker() -> None:
        raise RuntimeError("failed")

    supervisor = TaskSupervisor(observer=observer, observer_timeout=0.01)
    supervisor.register("worker", worker)
    await supervisor.start()
    await asyncio.wait_for(observer_started.wait(), timeout=0.2)

    async def wait_for_failed_state() -> None:
        while supervisor.infos[0].state is not TaskState.FAILED:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_failed_state(), timeout=0.2)
    assert not observer_done.is_set()
    observer_released.set()
    await asyncio.wait_for(observer_done.wait(), timeout=0.2)
    await supervisor.stop()


@pytest.mark.asyncio
async def test_supervisor_does_not_accumulate_timed_out_observers() -> None:
    observer_started = asyncio.Event()
    observer_released = asyncio.Event()
    observer_calls = 0

    async def observer(_: object) -> None:
        nonlocal observer_calls
        observer_calls += 1
        observer_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await observer_released.wait()

    attempts = 0

    async def worker() -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("transient")

    supervisor = TaskSupervisor(observer=observer, observer_timeout=0.01)
    supervisor.register(
        "worker", worker, policy=RestartPolicy.ON_FAILURE, max_restarts=2, restart_delay=0
    )
    await supervisor.start()
    await asyncio.wait_for(observer_started.wait(), timeout=0.2)

    async def wait_for_failed_state() -> None:
        while supervisor.infos[0].state is not TaskState.FAILED:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_failed_state(), timeout=0.2)
    assert attempts == 3
    assert observer_calls == 1
    observer_released.set()
    await supervisor.stop()


@pytest.mark.asyncio
async def test_supervisor_shutdown_drains_timed_out_observer_within_budget() -> None:
    observer_cancelled = asyncio.Event()
    observer_finished = asyncio.Event()
    release = asyncio.Event()

    async def observer(_: object) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            observer_cancelled.set()
            await release.wait()
            observer_finished.set()

    async def worker() -> None:
        raise RuntimeError("failed")

    supervisor = TaskSupervisor(observer=observer, observer_timeout=0.01, shutdown_timeout=0.1)
    supervisor.register("worker", worker)
    await supervisor.start()
    await asyncio.wait_for(observer_cancelled.wait(), timeout=0.2)

    stopping = asyncio.create_task(supervisor.stop())
    await asyncio.sleep(0)
    assert not stopping.done()
    release.set()
    await stopping
    assert observer_finished.is_set()


@pytest.mark.asyncio
async def test_supervisor_shutdown_isolates_late_observer_failure() -> None:
    cancellations = 0

    async def observer(_: object) -> None:
        nonlocal cancellations
        while True:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellations += 1
                if cancellations >= 2:
                    raise RuntimeError("late observer failure") from None

    async def worker() -> None:
        raise RuntimeError("failed")

    supervisor = TaskSupervisor(observer=observer, observer_timeout=0.01, shutdown_timeout=0.1)
    supervisor.register("worker", worker)
    await supervisor.start()

    async def wait_for_failed_state() -> None:
        while supervisor.infos[0].state is not TaskState.FAILED:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_failed_state(), timeout=0.2)
    assert supervisor.infos[0].state is TaskState.FAILED
    await supervisor.stop()
    assert cancellations >= 2


@pytest.mark.asyncio
async def test_supervisor_rejects_nonawaitable_factory_and_lifecycle_misuse() -> None:
    supervisor = TaskSupervisor()
    supervisor.register("bad", lambda: None)  # type: ignore[arg-type]
    await supervisor.start()
    await asyncio.sleep(0)
    assert supervisor.infos[0].state is TaskState.FAILED
    await supervisor.stop()
    await supervisor.stop()
    with pytest.raises(RuntimeError):
        supervisor.register("late", lambda: asyncio.sleep(0))


@pytest.mark.asyncio
async def test_shutdown_deadline_does_not_wait_forever_for_cancellation_suppressing_task() -> None:
    release = asyncio.Event()

    async def stubborn() -> None:
        while not release.is_set():
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                # Simulate a third-party task that refuses the host's cancellation request.
                continue

    supervisor = TaskSupervisor(shutdown_timeout=0.01)
    supervisor.register("stubborn", stubborn)
    await supervisor.start()
    await asyncio.sleep(0)

    await asyncio.wait_for(supervisor.stop(), timeout=0.2)
    assert supervisor.infos[0].state is TaskState.FAILED

    release.set()
    task = supervisor._tasks["stubborn"]  # noqa: SLF001 - release the test-owned task.
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_restart_deadline_rejects_cancellation_suppressing_task() -> None:
    release = asyncio.Event()

    async def stubborn() -> None:
        while not release.is_set():
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                continue

    supervisor = TaskSupervisor(shutdown_timeout=0.01)
    supervisor.register("stubborn", stubborn)
    await supervisor.start()
    await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="restart deadline"):
        await supervisor.restart("stubborn")
    assert supervisor.infos[0].state is TaskState.FAILED
    release.set()
    task = supervisor._tasks["stubborn"]  # noqa: SLF001 - release test-owned task.
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await supervisor.stop()
