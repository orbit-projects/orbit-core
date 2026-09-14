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
"""Deterministic concurrency regressions for provider ownership and cleanup."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from orbit.container import Container, Scope
from orbit.errors import ContainerError


async def test_request_scopes_do_not_serialize_unrelated_acquisition():
    root = Container()
    entered = 0
    both = asyncio.Event()

    async def factory(container):
        nonlocal entered
        entered += 1
        if entered == 2:
            both.set()
        await both.wait()
        return object()

    root.register_factory("request", factory, scope=Scope.SCOPED)
    async with root.scope() as first, root.scope() as second:
        async with asyncio.timeout(1):
            values = await asyncio.gather(first.aresolve("request"), second.aresolve("request"))
        assert values[0] is not values[1]
    await root.aclose()


async def test_forked_factory_resolution_fails_without_deadlock():
    root = Container()

    async def factory(container):
        return await asyncio.create_task(container.aresolve("inner"))

    root.register_factory("outer", factory)
    root.register_factory("inner", lambda _: object())
    async with asyncio.timeout(1):
        with pytest.raises(ContainerError, match="directly"):
            await root.aresolve("outer")
    assert await root.aresolve("inner") is not None
    await root.aclose()


async def test_sync_resolution_cannot_duplicate_pending_async_factory():
    root = Container()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def factory(container):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return object()

    root.register_factory("value", factory)
    pending = asyncio.create_task(root.aresolve("value"))
    await entered.wait()
    with pytest.raises(ContainerError, match="constructed"):
        root.resolve("value")
    release.set()
    value = await pending
    assert root.resolve("value") is value
    assert calls == 1
    await root.aclose()


async def test_close_waits_for_acquisition_and_closes_once():
    root = Container()
    entered, release = asyncio.Event(), asyncio.Event()
    closed = []

    @asynccontextmanager
    async def resource(container):
        entered.set()
        await release.wait()
        try:
            yield object()
        finally:
            closed.append(True)

    root.register_resource("value", resource)
    resolving = asyncio.create_task(root.aresolve("value"))
    await entered.wait()
    closing = asyncio.create_task(root.aclose())
    await asyncio.sleep(0)
    assert not closing.done()
    with pytest.raises(ContainerError, match="closed"):
        await root.aresolve("value")
    release.set()
    await resolving
    await asyncio.gather(closing, root.aclose())
    assert closed == [True]


async def test_cancelled_close_finishes_all_resources():
    root = Container()
    exiting, release = asyncio.Event(), asyncio.Event()
    closed = []

    @asynccontextmanager
    async def resource(container):
        try:
            yield object()
        finally:
            exiting.set()
            await release.wait()
            closed.append(True)

    root.register_resource("value", resource)
    await root.aresolve("value")
    closing = asyncio.create_task(root.aclose())
    await exiting.wait()
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    await root.aclose()
    assert closed == [True]


async def test_child_override_cannot_mutate_frozen_root():
    root = Container()
    root.register_factory("value", lambda _: "original")
    root.freeze()
    async with root.scope() as child:
        with pytest.raises(ContainerError), child.override("value", "changed"):
            pytest.fail("override must be rejected")
        assert child.resolve("value") == "original"
    await root.aclose()
