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

    Observers must be nonblocking. Async observers have a one-second timeout. Observer
    exceptions are logged; task cancellation still propagates. Application owns operation
    serialization; direct observers must not initiate lifecycle operations.
    """

    def __init__(self) -> None:
        self._phase = Phase.CREATED
        self._observers: list[LifecycleObserver] = []
        self._history: deque[LifecycleTransition] = deque(maxlen=100)

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
        self._observers.append(observer)

    async def transition(self, target: Phase) -> LifecycleTransition:
        """Commit a valid transition and notify all observers in registration order."""
        if target not in _ALLOWED[self._phase]:
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
        for observer in tuple(self._observers):
            try:
                outcome = observer(transition)
                if inspect.isawaitable(outcome):
                    async with asyncio.timeout(1):
                        await outcome
            except Exception:
                _LOG.exception("Lifecycle observer failed")
        return transition


__all__ = ["Lifecycle"]
