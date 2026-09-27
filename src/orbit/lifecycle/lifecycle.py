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
"""Validated transitions with bounded history and isolated observer failures."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import deque
from typing import Any

from orbit._limits import _MAX_CORE_CAPACITY, is_finite_number
from orbit.errors import ErrorCategory, LifecycleError, OrbitProblem
from orbit.lifecycle.contracts import LifecycleObserver
from orbit.lifecycle.phase import LifecyclePhase as Phase
from orbit.lifecycle.transition import LifecycleTransition

_LOG = logging.getLogger(__name__)
_ALLOWED = {
    Phase.CREATED: {Phase.CONFIGURED, Phase.STOPPING, Phase.FAILED},
    Phase.CONFIGURED: {Phase.INITIALIZED, Phase.STOPPING, Phase.FAILED},
    Phase.INITIALIZED: {Phase.STARTING, Phase.STOPPING, Phase.FAILED},
    Phase.STARTING: {Phase.RUNNING, Phase.STOPPING, Phase.FAILED},
    Phase.RUNNING: {Phase.STOPPING, Phase.FAILED},
    Phase.STOPPING: {Phase.STOPPED, Phase.FAILED},
    Phase.STOPPED: set(),
    Phase.FAILED: {Phase.STOPPING},
}


class Lifecycle:
    """Commit transitions before notifying observers; telemetry cannot undo state.

    Observers must be nonblocking. Async observers have a configurable deadline. A
    cancellation-resistant observer is detached once and skipped until it retires, so observer
    telemetry cannot block transitions or accumulate orphan tasks. Observer exceptions are logged;
    caller cancellation still propagates. Application owns operation serialization; direct
    observers must not initiate lifecycle operations.
    """

    def __init__(self, *, observer_timeout: float = 1.0) -> None:
        if (
            isinstance(observer_timeout, bool)
            or not isinstance(observer_timeout, (int, float))
            or not is_finite_number(observer_timeout)
            or observer_timeout <= 0
        ):
            raise ValueError("observer_timeout must be finite and positive.")
        self._phase = Phase.CREATED
        self._observers: list[LifecycleObserver] = []
        self._history: deque[LifecycleTransition] = deque(maxlen=100)
        self._observer_timeout = observer_timeout
        self._detached: dict[int, asyncio.Task[Any]] = {}

    @property
    def phase(self) -> Phase:
        """Return the current committed phase."""
        return self._phase

    @property
    def history(self) -> tuple[LifecycleTransition, ...]:
        """Return the latest 100 transitions, oldest first."""
        return tuple(self._history)

    def require(self, *phases: Phase) -> None:
        """Reject operations outside their allowed phases before performing work."""
        if self._phase not in phases:
            raise LifecycleError(
                OrbitProblem(
                    code="lifecycle.invalid-transition",
                    message=f"Operation requires {phases}; current phase is {self._phase}.",
                    category=ErrorCategory.LIFECYCLE,
                )
            )

    def observe(self, observer: LifecycleObserver) -> None:
        """Subscribe a nonblocking observer to committed transitions."""
        if not callable(observer):
            raise TypeError("Lifecycle observers must be callable.")
        if len(self._observers) >= _MAX_CORE_CAPACITY:
            raise LifecycleError(
                OrbitProblem(
                    code="lifecycle.capacity",
                    message="Lifecycle observer capacity reached.",
                    category=ErrorCategory.LIFECYCLE,
                )
            )
        self._observers.append(observer)

    def cancel_pending(self) -> None:
        """Request cancellation of observer tasks that outlived an earlier transition."""
        for task in tuple(self._detached.values()):
            if not task.done():
                task.cancel()

    async def transition(self, target: Phase) -> LifecycleTransition:
        """Commit a valid transition and notify all observers in registration order."""
        if not isinstance(target, Phase) or target not in _ALLOWED[self._phase]:
            raise LifecycleError(
                OrbitProblem(
                    code="lifecycle.invalid-transition",
                    message=f"Cannot transition from {self._phase} to {target}.",
                    category=ErrorCategory.LIFECYCLE,
                )
            )
        transition = LifecycleTransition(previous=self._phase, current=target)
        self._phase = target
        self._history.append(transition)
        for index, observer in enumerate(tuple(self._observers)):
            existing = self._detached.get(index)
            if existing is not None:
                if existing.done():
                    self._retire(index, existing)
                else:
                    continue
            task: asyncio.Task[Any] | None = None
            try:
                outcome = observer(transition)
                if inspect.isawaitable(outcome):
                    task = asyncio.ensure_future(outcome)
                    done, _ = await asyncio.wait({task}, timeout=self._observer_timeout)
                    if not done:
                        self._detach(index, task)
                        _LOG.error("Lifecycle observer exceeded its deadline")
                        continue
                    task.result()
            except asyncio.CancelledError:
                if task is not None and not task.done():
                    self._detach(index, task)
                raise
            except Exception:
                _LOG.exception("Lifecycle observer failed")
        return transition

    def _detach(self, index: int, task: asyncio.Task[Any]) -> None:
        """Retain one cancellation-resistant observer until its late result is consumed."""
        self._detached[index] = task
        task.cancel()
        task.add_done_callback(lambda finished: self._retire(index, finished))

    def _retire(self, index: int, task: asyncio.Task[Any]) -> None:
        """Forget a detached observer and consume its late result or exception."""
        if self._detached.get(index) is task:
            del self._detached[index]
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            return


__all__ = ["Lifecycle"]
