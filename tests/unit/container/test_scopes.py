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
"""Dependency graph, async singleton, scopes, overrides and resource cleanup contracts."""

import asyncio
from contextlib import asynccontextmanager, contextmanager

import pytest

from orbit.container import Container, Scope
from orbit.errors import ContainerError


async def test_scopes_isolate_instances_and_share_singletons():
    root = Container()
    root.register_factory("scoped", lambda _: object(), scope=Scope.SCOPED)
    root.register_factory("shared", lambda _: object())
    with pytest.raises(ContainerError):
        root.resolve("scoped")
    async with root.scope() as one, root.scope() as two:
        assert one.resolve("scoped") is one.resolve("scoped")
        assert one.resolve("scoped") is not two.resolve("scoped")
        assert one.resolve("shared") is two.resolve("shared")
    with pytest.raises(ContainerError):
        one.resolve("scoped")
    await root.aclose()


async def test_async_singleton_constructed_once_under_contention():
    root = Container()
    calls = []

    async def make(container):
        await asyncio.sleep(0.001)
        calls.append(1)
        return object()

    root.register_factory("one", make)
    results = await asyncio.gather(*(root.aresolve("one") for _ in range(20)))
    assert len(calls) == 1
    assert all(result is results[0] for result in results)
    await root.aclose()


async def test_nested_async_resolution_and_retry_after_failure():
    root = Container()

    async def make(container):
        return await container.aresolve("inner")

    root.register_factory("outer", make, dependencies=("inner",))
    with pytest.raises(ContainerError):
        root.validate()
    root.register_factory("inner", lambda _: object())
    root.freeze()
    assert await root.aresolve("outer") is await root.aresolve("inner")
    await root.aclose()


@pytest.mark.parametrize("scope", [Scope.SINGLETON, Scope.TRANSIENT, Scope.SCOPED])
async def test_resource_lifetimes_close_in_reverse_order(scope):
    root, calls = Container(), []

    @asynccontextmanager
    async def resource(container):
        calls.append("enter")
        try:
            yield object()
        finally:
            calls.append("exit")

    root.register_resource("r", resource, scope=scope)
    async with root.scope() as child:
        one = await child.aresolve("r")
        two = await child.aresolve("r")
        assert (one is two) == (scope is not Scope.TRANSIENT)
    if scope is Scope.SINGLETON:
        assert calls == ["enter"]
    else:
        assert calls[-1] == "exit"
    await root.aclose()
    assert calls.count("enter") == calls.count("exit")


async def test_sync_context_resources_are_owned():
    container = Container()
    calls = []

    @contextmanager
    def resource(current):
        try:
            yield 42
        finally:
            calls.append("close")

    container.register_resource("value", resource)
    with pytest.raises(ContainerError, match="aresolve"):
        container.resolve("value")
    assert await container.aresolve("value") == 42
    await container.aclose()
    await container.aclose()
    assert calls == ["close"]


def test_declared_cycles_and_captive_dependencies_are_rejected():
    container = Container()
    container.register_factory("a", lambda c: c.resolve("b"), dependencies=("b",))
    container.register_factory("b", lambda c: c.resolve("a"), dependencies=("a",))
    with pytest.raises(ContainerError, match="Circular"):
        container.validate()
    container = Container()
    container.register_factory("s", lambda c: c.resolve("r"), dependencies=("r",))
    container.register_factory("r", lambda c: object(), scope=Scope.SCOPED)
    with pytest.raises(ContainerError, match="Singleton"):
        container.validate()


async def test_dynamic_captive_dependency_is_rejected():
    root = Container()
    root.register_factory("singleton", lambda c: c.resolve("scoped"))
    root.register_factory("scoped", lambda c: object(), scope=Scope.SCOPED)
    async with root.scope() as child:
        with pytest.raises(ContainerError):
            child.resolve("singleton")
    await root.aclose()


async def test_async_factory_requires_async_api_without_leaking_coroutine():
    root = Container()

    async def make(c):
        return 42

    root.register_factory("async", make)
    with pytest.raises(ContainerError, match="aresolve"):
        root.resolve("async")
    assert await root.aresolve("async") == 42
    await root.aclose()


def test_override_restores_original_provider():
    container = Container()
    container.register_factory("value", lambda c: "original")
    with container.override("value", "override"):
        assert container.resolve("value") == "override"
    assert container.resolve("value") == "original"


async def test_root_cannot_close_with_active_scopes():
    root = Container()
    async with root.scope():
        with pytest.raises(ContainerError, match="scopes"):
            await root.aclose()
    await root.aclose()


def test_failed_factory_does_not_poison_resolution():
    root = Container()
    attempts = []

    def factory(c):
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError("retry")
        return None

    root.register_factory("x", factory)
    with pytest.raises(ValueError):
        root.resolve("x")
    assert root.resolve("x") is None
    assert root.resolve("x") is None
    assert len(attempts) == 2
