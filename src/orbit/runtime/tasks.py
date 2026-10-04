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
"""Supervised application background tasks with bounded restart and shutdown semantics.

The supervisor owns tasks created during one application lifecycle. Task factories are
called once per attempt in the current event loop; they must not perform blocking work.
Restart is explicit and bounded. Failures are retained as payload-free metadata and sent
to an optional observer without allowing observer errors or an indefinitely waiting async
observer to escape the supervisor. At most one cancellation-resistant observer is retained
after a timeout; later failures continue to update task state without creating more orphan tasks.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from typing import Any

from orbit._limits import _MAX_CORE_CAPACITY, is_finite_number, safe_exception_type_name

_LOG = logging.getLogger(__name__)
TaskFactory = Callable[[], Awaitable[None]]
TaskObserver = Callable[["TaskFailure"], Awaitable[None] | None]
_TASK_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,126}")
_MAX_TASK_HISTORY = 1_000_000
_MAX_TASK_RESTARTS = 1_000_000


def _validate_task_name(name: str) -> None:
    """Apply the same bounded identifier contract to registration and snapshots."""
    if not isinstance(name, str) or _TASK_NAME_PATTERN.fullmatch(name) is None:
        raise ValueError("Task names must be unique lowercase identifiers.")


class RestartPolicy(StrEnum):
    """How a task reacts to an unhandled exception."""

    NEVER = "never"
    ON_FAILURE = "on-failure"


class TaskState(StrEnum):
    """Observable state of a supervised task."""

    REGISTERED = "registered"
    RUNNING = "running"
    RESTARTING = "restarting"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskFailure:
    """Payload-free failure metadata safe for diagnostics and audit events."""

    name: str
    attempt: int
    error_type: str
    elapsed_seconds: float

    def __post_init__(self) -> None:
        """Validate the detached failure record before it reaches diagnostics or events."""
        _validate_task_name(self.name)
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            raise ValueError("Task failure attempts must be positive integers.")
        if (
            not isinstance(self.error_type, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", self.error_type) is None
        ):
            raise ValueError("Task failure error types must be bounded identifiers.")
        if (
            isinstance(self.elapsed_seconds, bool)
            or not isinstance(self.elapsed_seconds, (int, float))
            or not is_finite_number(self.elapsed_seconds)
            or self.elapsed_seconds < 0
        ):
            raise ValueError("Task failure elapsed time must be finite and nonnegative.")
        object.__setattr__(self, "elapsed_seconds", float(self.elapsed_seconds))


@dataclass(frozen=True)
class TaskInfo:
    """Detached task state returned by supervisor inspection."""

    name: str
    state: TaskState
    attempts: int
    last_failure: TaskFailure | None

    def __post_init__(self) -> None:
        """Validate the public task snapshot independently of supervisor internals."""
        _validate_task_name(self.name)
        if not isinstance(self.state, TaskState):
            raise TypeError("Task state must be a TaskState.")
        if (
            isinstance(self.attempts, bool)
            or not isinstance(self.attempts, int)
            or self.attempts < 0
        ):
            raise ValueError("Task attempts must be nonnegative integers.")
        if self.last_failure is not None and not isinstance(self.last_failure, TaskFailure):
            raise TypeError("Task last_failure must be a TaskFailure or None.")


@dataclass(frozen=True)
class _TaskSpec:
    name: str
    factory: TaskFactory
    policy: RestartPolicy
    max_restarts: int
    restart_delay: float


class TaskSupervisor:
    """Own, supervise, inspect and cancel background tasks for one application.

    Registration is composition-only and rejects duplicate names. A task that exits
    normally is considered stopped. An exception either marks it failed or triggers a
    finite restart sequence. Cancellation from supervisor shutdown is treated as an
    intentional stop and never restarted.
    """

    def __init__(
        self,
        *,
        shutdown_timeout: float = 30,
        observer_timeout: float = 1,
        history_size: int = 100,
        observer: TaskObserver | None = None,
    ) -> None:
        if (
            isinstance(shutdown_timeout, bool)
            or not isinstance(shutdown_timeout, (int, float))
            or not is_finite_number(shutdown_timeout)
            or shutdown_timeout <= 0
        ):
            raise ValueError("shutdown_timeout must be positive.")
        if (
            isinstance(observer_timeout, bool)
            or not isinstance(observer_timeout, (int, float))
            or not is_finite_number(observer_timeout)
            or observer_timeout <= 0
        ):
            raise ValueError("observer_timeout must be positive.")
        if (
            isinstance(history_size, bool)
            or not isinstance(history_size, int)
            or not 0 <= history_size <= _MAX_TASK_HISTORY
        ):
            raise ValueError("history_size must be between 0 and 1,000,000.")
        if observer is not None and not callable(observer):
            raise TypeError("Task observer must be callable.")
        self._shutdown_timeout = shutdown_timeout
        self._observer_timeout = observer_timeout
        self._observer = observer
        self._specs: dict[str, _TaskSpec] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._states: dict[str, TaskState] = {}
        self._attempts: dict[str, int] = {}
        self._failures: dict[str, TaskFailure | None] = {}
        self._history: deque[TaskFailure] = deque(maxlen=history_size)
        self._started = False
        self._stopping = False
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None
        self._operation_lock = asyncio.Lock()
        self._abandoned: set[str] = set()
        self._restarting: set[str] = set()
        self._detached_observer: asyncio.Future[Any] | None = None

    @property
    def infos(self) -> tuple[TaskInfo, ...]:
        """Return deterministic task snapshots without factories or exception messages."""
        return tuple(
            TaskInfo(name, self._states[name], self._attempts[name], self._failures[name])
            for name in self._specs
        )

    @property
    def history(self) -> tuple[TaskFailure, ...]:
        """Return bounded failure metadata in chronological order."""
        return tuple(self._history)

    def register(
        self,
        name: str,
        factory: TaskFactory,
        *,
        policy: RestartPolicy = RestartPolicy.NEVER,
        max_restarts: int = 0,
        restart_delay: float = 0.1,
    ) -> None:
        """Register a named task before startup with an explicit restart budget."""
        if self._started or self._closed:
            raise RuntimeError("Task registration is closed after supervisor startup.")
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[a-z][a-z0-9_.-]{0,126}", name) is None
            or name in self._specs
        ):
            raise ValueError("Task names must be unique lowercase identifiers.")
        if len(self._specs) >= _MAX_CORE_CAPACITY:
            raise RuntimeError("Task registration capacity reached.")
        if not callable(factory):
            raise TypeError("Task factory must be callable.")
        if (
            isinstance(max_restarts, bool)
            or not isinstance(max_restarts, int)
            or not 0 <= max_restarts <= _MAX_TASK_RESTARTS
            or isinstance(restart_delay, bool)
            or not isinstance(restart_delay, (int, float))
            or not is_finite_number(restart_delay)
            or restart_delay < 0
        ):
            raise ValueError(
                "Restart limits must be between 0 and 1,000,000; delay cannot be negative."
            )
        if not isinstance(policy, RestartPolicy):
            raise TypeError("policy must be a RestartPolicy.")
        if policy is RestartPolicy.NEVER and max_restarts:
            raise ValueError("max_restarts requires the ON_FAILURE policy.")
        if policy is RestartPolicy.ON_FAILURE and max_restarts == 0:
            raise ValueError("ON_FAILURE requires a positive max_restarts budget.")
        self._specs[name] = _TaskSpec(name, factory, policy, max_restarts, restart_delay)
        self._states[name] = TaskState.REGISTERED
        self._attempts[name] = 0
        self._failures[name] = None

    async def start(self) -> None:
        """Start all registered tasks exactly once in registration order."""
        async with self._operation_lock:
            if self._started:
                raise RuntimeError("Task supervisor has already started.")
            if self._closed:
                raise RuntimeError("Task supervisor is closed.")
            self._started = True
            for spec in self._specs.values():
                self._tasks[spec.name] = asyncio.create_task(
                    self._run(spec), name=f"orbit:{spec.name}"
                )

    async def restart(self, name: str) -> None:
        """Restart one supervised task while the application remains running.

        The existing attempt is cancelled and fully joined before a replacement is
        created. Restarting never changes the task's configured policy or factory and
        does not erase failure history, which keeps operational diagnostics truthful.
        """
        if not isinstance(name, str):
            raise TypeError("Task names must be strings.")
        if re.fullmatch(r"[a-z][a-z0-9_.-]{0,126}", name) is None:
            raise ValueError("Task names must be lowercase identifiers.")
        async with self._operation_lock:
            if not self._started or self._closed or self._stopping:
                raise RuntimeError("Task supervisor is not running.")
            spec = self._specs.get(name)
            if spec is None:
                raise KeyError(name)
            current = self._tasks.get(name)
            if current is not None and not current.done():
                self._restarting.add(name)
                try:
                    current.cancel()
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(current), timeout=self._shutdown_timeout
                        )
                    except TimeoutError as exc:
                        self._abandoned.add(name)
                        self._states[name] = TaskState.FAILED
                        current.add_done_callback(_consume_task_result)
                        raise RuntimeError(
                            f"Task {name!r} did not terminate before restart deadline."
                        ) from exc
                    except asyncio.CancelledError:
                        # A cancellation from the current task is distinct from the
                        # cancellation delivered to the supervised task through the shield.
                        # Do not swallow operator cancellation or leave a replacement half-built.
                        operation = asyncio.current_task()
                        operator_cancelled = operation is not None and operation.cancelling() > 0
                        if not current.cancelled() or operator_cancelled:
                            self._abandoned.add(name)
                            self._states[name] = TaskState.FAILED
                            current.add_done_callback(_consume_task_result)
                            raise
                    except Exception:
                        # Explicit restart treats any terminal outcome from the old task as
                        # completed cancellation. The replacement owns the next attempt, and
                        # the awaited task has already had its exception retrieved.
                        pass
                finally:
                    self._restarting.discard(name)
            self._abandoned.discard(name)
            self._states[name] = TaskState.REGISTERED
            self._tasks[name] = asyncio.create_task(self._run(spec), name=f"orbit:{name}")

    async def _run(self, spec: _TaskSpec) -> None:
        restarts = 0
        while not self._stopping:
            self._attempts[spec.name] += 1
            self._states[spec.name] = TaskState.RUNNING
            started = monotonic()
            try:
                result = spec.factory()
                if not inspect.isawaitable(result):
                    raise TypeError("Task factory must return an awaitable.")
                await result
                if not self._stopping:
                    self._states[spec.name] = TaskState.STOPPED
                return
            except asyncio.CancelledError:
                intentional = spec.name in self._abandoned or spec.name in self._restarting
                if not intentional:
                    self._states[spec.name] = (
                        TaskState.STOPPED if self._stopping else TaskState.FAILED
                    )
                if not self._stopping and not intentional:
                    await self._record_failure(spec, started, "CancelledError")
                return
            except Exception as exc:
                await self._record_failure(spec, started, safe_exception_type_name(exc))
                if (
                    self._stopping
                    or spec.policy is RestartPolicy.NEVER
                    or restarts >= spec.max_restarts
                ):
                    self._states[spec.name] = TaskState.FAILED
                    return
                restarts += 1
                self._states[spec.name] = TaskState.RESTARTING
                if spec.restart_delay:
                    await asyncio.sleep(spec.restart_delay)

    async def _record_failure(
        self, spec: _TaskSpec, started: float, error_type: str
    ) -> TaskFailure:
        failure = TaskFailure(
            spec.name, self._attempts[spec.name], error_type, monotonic() - started
        )
        self._failures[spec.name] = failure
        self._history.append(failure)
        if self._observer is not None:
            detached = self._detached_observer
            if detached is not None:
                if detached.done():
                    self._retire_observer(detached)
                else:
                    _LOG.error(
                        "Background task observer is still cancelling; skipping observer call",
                        extra={"task": spec.name},
                    )
                    return failure
            observer_task: asyncio.Future[Any] | None = None
            try:
                outcome = self._observer(failure)
                if inspect.isawaitable(outcome):
                    observer_task = asyncio.ensure_future(outcome)
                    await asyncio.wait_for(
                        asyncio.shield(observer_task), timeout=self._observer_timeout
                    )
            except TimeoutError:
                if observer_task is not None:
                    self._detach_observer(observer_task)
                _LOG.error(
                    "Background task observer exceeded its deadline",
                    extra={"task": spec.name},
                )
            except asyncio.CancelledError:
                if observer_task is not None:
                    self._detach_observer(observer_task)
                raise
            except Exception:
                _LOG.exception("Background task observer failed", extra={"task": spec.name})
        return failure

    def _detach_observer(self, task: asyncio.Future[Any]) -> None:
        """Cancel and retain one timed-out observer until it actually finishes."""
        self._detached_observer = task
        task.cancel()
        task.add_done_callback(self._retire_observer)

    def _retire_observer(self, task: asyncio.Future[Any]) -> None:
        """Forget a detached observer and consume its eventual result or exception."""
        if self._detached_observer is task:
            self._detached_observer = None
        _consume_future_result(task)

    async def stop(self) -> None:
        """Cancel tasks and await bounded cleanup before completing shutdown.

        Concurrent callers share one shutdown operation. Caller cancellation is deferred
        until cleanup finishes or reaches its deadline, then re-raised to the caller.
        """
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._stop())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _stop(self) -> None:
        async with self._operation_lock:
            self._stopping = True
            deadline = monotonic() + self._shutdown_timeout
            for name in self._tasks:
                if self._states[name] in {TaskState.RUNNING, TaskState.RESTARTING}:
                    self._states[name] = TaskState.STOPPING
            for task in self._tasks.values():
                if not task.done():
                    task.cancel()
            if self._tasks:
                _, pending = await asyncio.wait(
                    self._tasks.values(), timeout=self._shutdown_timeout
                )
                if pending:
                    for task in pending:
                        task.cancel()
                    remaining = max(0.0, deadline - monotonic())
                    if remaining:
                        _, pending = await asyncio.wait(pending, timeout=remaining)
                    if pending:
                        # A Python task cannot be forcefully terminated. Keep shutdown
                        # bounded, retain an explicit failure state, and consume any
                        # eventual exception so the event loop does not emit warnings.
                        for task in pending:
                            name = task.get_name().removeprefix("orbit:")
                            self._abandoned.add(name)
                            self._states[name] = TaskState.FAILED
                            task.add_done_callback(_consume_task_result)
                        _LOG.error(
                            "Background tasks did not terminate before shutdown deadline",
                            extra={"tasks": sorted(self._abandoned)},
                        )
            if self._detached_observer is not None and not self._detached_observer.done():
                observer = self._detached_observer
                observer.cancel()
                remaining = max(0.0, deadline - monotonic())
                if remaining:
                    try:
                        await asyncio.wait_for(asyncio.shield(observer), timeout=remaining)
                    except TimeoutError:
                        _LOG.error("Background task observer did not terminate before shutdown")
                    except asyncio.CancelledError:
                        # The observer itself may suppress cancellation; its done callback still
                        # consumes the eventual result after the bounded supervisor close.
                        pass
                    except Exception:
                        _LOG.exception("Background task observer failed during shutdown")
            for name, state in self._states.items():
                if state is TaskState.STOPPING:
                    self._states[name] = TaskState.STOPPED
            self._closed = True


def _consume_future_result(task: asyncio.Future[Any]) -> None:
    """Consume an abandoned observer result without warnings."""
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        return


def _consume_task_result(task: asyncio.Task[None]) -> None:
    """Consume an abandoned task's eventual result without affecting shutdown."""
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        return


__all__ = ["RestartPolicy", "TaskFailure", "TaskInfo", "TaskState", "TaskSupervisor"]
