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

    def __init__(self, *, _parent: Container | None = None) -> None:
        self._root: Container = _parent._root if _parent else self
        self._providers: dict[DependencyKey, Provider] = self._root._providers if _parent else {}
        self._cache: dict[DependencyKey, object] = {}
        self._stack = AsyncExitStack()
        self._frozen = False
        self._closed = False
        self._async_lock = asyncio.Lock()
        self._sync_lock = RLock()
        self._scopes: set[Container] = set()

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
        if self._closed or self._root._closed:
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
        """Resolve async or sync providers, entering owned resources exactly once."""
        if _path.get():
            return await self._aresolve(key)
        async with self._root._async_lock:
            return await self._aresolve(key)

    async def _aresolve(self, key: DependencyKey) -> object:
        provider, owner = self._prepare(key)
        if provider.scope is not Scope.TRANSIENT and key in owner._cache:
            return owner._cache[key]
        token = _path.set((*_path.get(), (key, provider.scope)))
        try:
            instance = provider.factory(owner)
            if inspect.isawaitable(instance):
                instance = await instance
            if provider.resource:
                if hasattr(instance, "__aenter__"):
                    instance = await owner._stack.enter_async_context(instance)
                else:
                    instance = owner._stack.enter_context(instance)
            if provider.scope is not Scope.TRANSIENT:
                owner._cache[key] = instance
            return cast(object, instance)
        finally:
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
        if self._frozen or self._cache:
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
        """Close resources in reverse acquisition order; closing is idempotent."""
        if self._closed:
            return
        if self is self._root and self._scopes:
            raise _error("active-scopes", "Close request scopes before closing the application.")
        self._closed = True
        try:
            await self._stack.aclose()
        finally:
            self._cache.clear()


__all__ = ["Container"]
