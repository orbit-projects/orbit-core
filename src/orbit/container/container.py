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
"""Scoped dependency resolution with graph validation and deterministic resource cleanup.

Factories receive their resolving container. Async factories and context-manager resources
must be resolved with aresolve. Singleton construction is serialized on the root container;
nested resolution reuses the same task's transaction. Container instances belong to one
event loop. Synchronous resolution is protected against concurrent threads.
"""

from __future__ import annotations

import asyncio
import inspect
import math
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from contextvars import ContextVar
from threading import RLock
from typing import Any, TypeVar, cast, overload

from orbit.container.dependency import DependencyKey
from orbit.container.provider import Provider
from orbit.container.scope import Scope
from orbit.errors import ContainerError, ErrorCategory, OrbitProblem

T = TypeVar("T")
_path: ContextVar[tuple[tuple[DependencyKey, Scope], ...]] = ContextVar(
    "orbit_resolution", default=()
)
_transactions: ContextVar[tuple[tuple[Container, asyncio.Task[Any]], ...]] = ContextVar(
    "orbit_container_transactions", default=()
)


def _error(code: str, message: str) -> ContainerError:
    return ContainerError(
        OrbitProblem(code="container." + code, message=message, category=ErrorCategory.CONTAINER)
    )


class Container:
    """Register typed factories and own their lifetimes.

    Registration ends at freeze(). Call validate() before acquiring resources.
    Every resource must have an explicit context manager; arbitrary objects are never
    closed by guessing a close() method.
    """

    def __init__(self, *, cleanup_timeout: float = 30, _parent: Container | None = None) -> None:
        if not math.isfinite(cleanup_timeout) or cleanup_timeout <= 0:
            raise ValueError("cleanup_timeout must be finite and positive.")
        self._root: Container = _parent._root if _parent else self
        self._cleanup_timeout: float = _parent._cleanup_timeout if _parent else cleanup_timeout
        self._cleanup_errors: list[Exception] = []
        self._providers: dict[DependencyKey, Provider] = self._root._providers if _parent else {}
        self._cache: dict[DependencyKey, object] = {}
        self._stack = AsyncExitStack()
        self._frozen = False
        self._closed = False
        self._async_lock = asyncio.Lock()
        self._sync_lock = RLock()
        self._scopes: set[Container] = set()
        self._resolving: set[DependencyKey] = set()
        self._closing: asyncio.Task[None] | None = None

    @property
    def providers(self) -> dict[DependencyKey, Provider]:
        """Return a copy of provider definitions for diagnostics."""
        return dict(self._providers)

    def freeze(self) -> None:
        """Validate the graph and prohibit changes during runtime."""
        self.validate()
        self._root._frozen = True

    def register_instance(self, key: type[T] | str, instance: T, *, replace: bool = False) -> None:
        """Register an externally owned instance; Orbit will not close it."""
        self.register_factory(key, lambda _: instance, replace=replace)
        self._cache[key] = instance

    def register_factory(
        self,
        key: type[T] | str,
        factory: Callable[[Container], T],
        *,
        scope: Scope = Scope.SINGLETON,
        dependencies: tuple[DependencyKey, ...] = (),
        replace: bool = False,
    ) -> None:
        """Register a lazy factory; declare dependencies to enable startup validation."""
        self._register(key, Provider(factory, scope, dependencies), replace)

    def register_resource(
        self,
        key: DependencyKey,
        factory: Callable[[Container], Any],
        *,
        scope: Scope = Scope.SINGLETON,
        dependencies: tuple[DependencyKey, ...] = (),
    ) -> None:
        """Register a sync/async context manager, entered by aresolve and exited at scope end."""
        self._register(key, Provider(factory, scope, dependencies, resource=True), False)

    def _register(self, key: DependencyKey, provider: Provider, replace: bool) -> None:
        self._ensure_open()
        if self is not self._root or self._root._frozen:
            raise _error("frozen", "Provider registration is closed.")
        if key in self._providers and not replace:
            raise _error("duplicate-provider", f"A provider is already registered for {key}.")
        if key in self._cache and replace:
            raise _error("resolved-provider", "Cannot replace a resolved provider.")
        self._providers[key] = provider

    def validate(self) -> None:
        """Reject missing providers, cycles and singleton dependencies on scoped values."""

        def visit(key: DependencyKey, path: tuple[DependencyKey, ...], singleton: bool) -> None:
            if key in path:
                raise _error("circular-dependency", f"Circular dependency: {(*path, key)}")
            provider = self._providers.get(key)
            if provider is None:
                raise _error("provider-not-found", f"No provider registered for {key}.")
            if singleton and provider.scope is Scope.SCOPED:
                raise _error("captive-dependency", f"Singleton cannot depend on scoped {key}.")
            for dep in provider.dependencies:
                visit(dep, (*path, key), singleton or provider.scope is Scope.SINGLETON)

        for key in self._providers:
            visit(key, (), False)

    def _ensure_open(self) -> None:
        if self._closed or self._root._closed or self._closing or self._root._closing:
            raise _error("closed", "The dependency scope is closed.")

    def _prepare(self, key: DependencyKey) -> tuple[Provider, Container]:
        self._ensure_open()
        provider = self._providers.get(key)
        if provider is None:
            raise _error("provider-not-found", f"No provider registered for {key}.")
        path = _path.get()
        if any(k == key for k, _ in path):
            raise _error("circular-dependency", f"Circular dependency while resolving {key}.")
        if provider.scope is Scope.SCOPED:
            if self is self._root:
                raise _error("scope-required", f"{key} requires an active child scope.")
            if any(s is Scope.SINGLETON for _, s in path):
                raise _error("captive-dependency", "Singleton cannot capture a scoped dependency.")
        return provider, self._root if provider.scope is Scope.SINGLETON else self

    @overload
    def resolve(self, key: type[T]) -> T: ...
    @overload
    def resolve(self, key: str) -> Any: ...
    def resolve(self, key: DependencyKey) -> Any:
        """Resolve synchronous values, caching singleton and scoped instances.

        Async factories and managed resources raise ContainerError; use aresolve for those.
        Failed factories are never cached and leave the resolution stack intact.
        """
        with self._root._sync_lock:
            provider, owner = self._prepare(key)
            if key in owner._resolving:
                raise _error("async-in-progress", "Provider is being constructed; use aresolve().")
            if provider.scope is not Scope.TRANSIENT and key in owner._cache:
                return owner._cache[key]
            if provider.resource:
                raise _error("async-required", "Resources require aresolve().")
            token = _path.set((*_path.get(), (key, provider.scope)))
            try:
                instance = provider.factory(owner)
                if inspect.isawaitable(instance):
                    if inspect.iscoroutine(instance):
                        instance.close()
                    raise _error("async-required", "Async factories require aresolve().")
                if provider.scope is not Scope.TRANSIENT:
                    owner._cache[key] = instance
                return instance
            finally:
                _path.reset(token)

    @overload
    async def aresolve(self, key: type[T]) -> T: ...
    @overload
    async def aresolve(self, key: str) -> Any: ...
    async def aresolve(self, key: DependencyKey) -> Any:
        """Resolve a provider under its owning scope's construction transaction.

        Independent request scopes construct concurrently. Singleton construction uses
        the root lock. Nested resolution must be awaited directly: spawning a task from
        inside a factory is rejected rather than inheriting permission to bypass locks.
        """
        _, owner = self._prepare(key)
        task = asyncio.current_task()
        assert task is not None
        transactions = _transactions.get()
        if any(holder is not task for _, holder in transactions):
            raise _error("forked-resolution", "Await nested resolution directly inside factories.")
        if any(container is owner for container, _ in transactions):
            return await self._aresolve(key)
        async with owner._async_lock:
            token = _transactions.set((*transactions, (owner, task)))
            try:
                return await self._aresolve(key)
            finally:
                _transactions.reset(token)

    async def _aresolve(self, key: DependencyKey) -> object:
        provider, owner = self._prepare(key)
        if provider.scope is not Scope.TRANSIENT and key in owner._cache:
            return owner._cache[key]
        token = _path.set((*_path.get(), (key, provider.scope)))
        owner._resolving.add(key)
        try:
            instance = provider.factory(owner)
            if inspect.isawaitable(instance):
                instance = await instance
            if provider.resource:
                if hasattr(instance, "__aenter__"):
                    manager = instance
                    instance = await manager.__aenter__()
                    owner._stack.push_async_callback(owner._release_resource, manager, True)
                else:
                    manager = instance
                    instance = manager.__enter__()
                    owner._stack.push_async_callback(owner._release_resource, manager, False)
            if provider.scope is not Scope.TRANSIENT:
                owner._cache[key] = instance
            return cast(object, instance)
        finally:
            owner._resolving.discard(key)
            _path.reset(token)

    @asynccontextmanager
    async def scope(self) -> AsyncIterator[Container]:
        """Create an isolated request/task scope and close it on every exit path."""
        self._ensure_open()
        child = Container(_parent=self)
        self._root._scopes.add(child)
        try:
            yield child
        finally:
            try:
                await child.aclose()
            finally:
                self._root._scopes.discard(child)

    @contextmanager
    def override(self, key: DependencyKey, instance: object) -> Iterator[None]:
        """Temporarily override an unresolved provider during composition, for tests.

        Overrides are deliberately forbidden after freeze or resolution, so cached
        dependents cannot retain an inconsistent graph.
        """
        self._ensure_open()
        if self is not self._root or self._frozen or self._cache or self._scopes:
            raise _error("override-unavailable", "Override before resolving or freezing providers.")
        old = self._providers.get(key)
        self._providers[key] = Provider(lambda _: instance)
        try:
            yield
        finally:
            self._cache.clear()
            if old is None:
                del self._providers[key]
            else:
                self._providers[key] = old

    async def aclose(self) -> None:
        """Wait for acquisition, then close resources exactly once in reverse order.

        Cancellation of a caller is deferred until cleanup completes. Concurrent callers
        await the same cleanup task and observe the same failure. New resolutions are
        rejected as soon as closing begins. Close child scopes before the root.
        """
        if _transactions.get():
            raise _error("close-during-resolution", "Cannot close a scope inside a factory.")
        if self._closing is None:
            if self is self._root and self._scopes:
                raise _error(
                    "active-scopes", "Close request scopes before closing the application."
                )
            self._closing = asyncio.create_task(self._close_resources())
        cancelled = False
        while not self._closing.done():
            try:
                await asyncio.shield(self._closing)
            except asyncio.CancelledError:
                cancelled = True
        self._closing.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close_resources(self) -> None:
        async with self._async_lock:
            await self._exit_resources()

    async def _exit_resources(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._stack.aclose()
        finally:
            self._cache.clear()
        if self._cleanup_errors:
            raise ExceptionGroup("Provider resource cleanup failed", self._cleanup_errors)

    async def _release_resource(self, manager: Any, asynchronous: bool) -> None:
        # Each resource gets its own deadline so one failure cannot skip later exits.
        try:
            if asynchronous:
                async with asyncio.timeout(self._cleanup_timeout):
                    await manager.__aexit__(None, None, None)
            else:
                manager.__exit__(None, None, None)
        except (Exception, asyncio.CancelledError) as exc:
            self._cleanup_errors.append(
                exc if isinstance(exc, Exception) else RuntimeError("Resource exit cancelled.")
            )


__all__ = ["Container"]
