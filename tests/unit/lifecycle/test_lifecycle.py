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
"""Lifecycle validation, timeout and observer ownership tests."""

import asyncio
from datetime import datetime

import pytest

from orbit.errors import LifecycleError
from orbit.lifecycle import Lifecycle, LifecyclePhase
from orbit.lifecycle.transition import LifecycleTransition


async def test_lifecycle_rejects_skipped_phases() -> None:
    """The lifecycle state machine rejects an invalid direct startup transition."""
    lifecycle = Lifecycle()

    with pytest.raises(LifecycleError, match="Cannot transition"):
        await lifecycle.transition(LifecyclePhase.RUNNING)


def test_lifecycle_rejects_non_callable_observers() -> None:
    lifecycle = Lifecycle()
    with pytest.raises(TypeError, match="observers"):
        lifecycle.observe(object())  # type: ignore[arg-type]


def test_lifecycle_observer_capacity_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Observer registration cannot grow unbounded transition fan-out state."""
    monkeypatch.setattr("orbit.lifecycle.lifecycle._MAX_CORE_CAPACITY", 1)
    lifecycle = Lifecycle()
    lifecycle.observe(lambda transition: None)
    with pytest.raises(LifecycleError, match="capacity"):
        lifecycle.observe(lambda transition: None)


def test_lifecycle_rejects_invalid_observer_timeout() -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        Lifecycle(observer_timeout=0)
    with pytest.raises(ValueError, match="finite and positive"):
        Lifecycle(observer_timeout=float("inf"))
    with pytest.raises(ValueError, match="finite and positive"):
        Lifecycle(observer_timeout=True)  # type: ignore[arg-type]


def test_lifecycle_transition_requires_aware_timestamp() -> None:
    """History entries cannot carry timezone-ambiguous transition times."""
    with pytest.raises(ValueError, match="timezone"):
        LifecycleTransition(
            previous=LifecyclePhase.CREATED,
            current=LifecyclePhase.CONFIGURED,
            occurred_at=datetime(2026, 1, 1),
        )


@pytest.mark.asyncio
async def test_lifecycle_rejects_invalid_transition_targets() -> None:
    lifecycle = Lifecycle()
    with pytest.raises(LifecycleError, match="Cannot transition"):
        await lifecycle.transition("running")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_cancellation_resistant_observer_is_detached_until_it_retires() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def observer(transition) -> None:
        nonlocal calls
        calls += 1
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    lifecycle = Lifecycle(observer_timeout=0.01)
    lifecycle.observe(observer)
    await lifecycle.transition(LifecyclePhase.CONFIGURED)
    assert started.is_set()
    assert calls == 1

    await lifecycle.transition(LifecyclePhase.INITIALIZED)
    assert calls == 1

    release.set()
    for _ in range(100):
        await asyncio.sleep(0.001)
        if not lifecycle._detached:  # noqa: SLF001 - wait for test-owned observer task.
            break
    assert not lifecycle._detached  # noqa: SLF001 - verify late observer retirement.

    await lifecycle.transition(LifecyclePhase.STARTING)
    assert calls == 2
