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
"""Every resource exit receives a deadline and failures remain observable."""

import asyncio
import threading
from contextlib import asynccontextmanager, contextmanager

import pytest

from orbit.container import Container


async def test_cleanup_aggregates_timeout_and_errors_and_attempts_every_exit():
    root = Container(cleanup_timeout=0.01)
    exits = []

    @asynccontextmanager
    async def first(container):
        yield "first"
        exits.append("first")
        raise ValueError("first")

    @asynccontextmanager
    async def last(container):
        yield "last"
        exits.append("last")
        await asyncio.Event().wait()

    root.register_resource("first", first)
    root.register_resource("last", last)
    await root.aresolve("first")
    await root.aresolve("last")
    with pytest.raises(ExceptionGroup) as error:
        await root.aclose()
    assert exits == ["last", "first"]
    assert {type(exc) for exc in error.value.exceptions} == {TimeoutError, ValueError}
    with pytest.raises(ExceptionGroup):
        await root.aclose()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "1"])
def test_cleanup_deadline_must_be_positive_finite(timeout):
    with pytest.raises(ValueError):
        Container(cleanup_timeout=timeout)


async def test_blocking_synchronous_exit_is_bounded_without_blocking_event_loop():
    root = Container(cleanup_timeout=0.01)
    entered = threading.Event()
    release = threading.Event()

    @contextmanager
    def blocking(container):
        yield "value"
        entered.set()
        release.wait()

    root.register_resource("blocking", blocking)
    assert await root.aresolve("blocking") == "value"
    with pytest.raises(ExceptionGroup, match="cleanup failed"):
        await asyncio.wait_for(root.aclose(), timeout=0.2)
    assert entered.is_set()
    release.set()


async def test_cancellation_resistant_async_exit_is_detached_and_later_exits_run():
    root = Container(cleanup_timeout=0.01)
    started = asyncio.Event()
    release = asyncio.Event()
    exits = []

    @asynccontextmanager
    async def stubborn(container):
        yield "stubborn"
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
        exits.append("stubborn")

    @asynccontextmanager
    async def ordinary(container):
        yield "ordinary"
        exits.append("ordinary")

    root.register_resource("stubborn", stubborn)
    root.register_resource("ordinary", ordinary)
    await root.aresolve("stubborn")
    await root.aresolve("ordinary")
    with pytest.raises(ExceptionGroup, match="cleanup failed"):
        await asyncio.wait_for(root.aclose(), timeout=0.1)
    assert started.is_set()
    assert exits == ["ordinary"]

    release.set()
    for _ in range(20):
        await asyncio.sleep(0)
        if not root._detached_exits:  # noqa: SLF001 - wait for test-owned cleanup.
            break
    assert exits == ["ordinary", "stubborn"]
    assert not root._detached_exits  # noqa: SLF001 - verify detached exit retirement.
