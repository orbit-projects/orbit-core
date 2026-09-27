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
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock, Thread
from time import monotonic
from typing import Any, TypeVar, cast, overload

from orbit._limits import _MAX_CORE_CAPACITY, _MAX_RELATION_ENTRIES, is_finite_number
from orbit.container.dependency import DependencyKey, _validate_dependency_key
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
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderResolution:
    """Validated, payload-free provider resolution outcome for diagnostics and metrics."""

    key: DependencyKey
    scope: Scope
    success: bool
    elapsed_seconds: float
    error_type: str | None = None

    def __post_init__(self) -> None:
        """Validate resolution metadata before it reaches observers or telemetry exporters."""
        _validate_dependency_key(self.key)
        if not isinstance(self.scope, Scope):
            raise TypeError("Provider resolution scope must be a Scope.")
        if not isinstance(self.success, bool):
            raise TypeError("Provider resolution success must be a boolean.")
        if (
            isinstance(self.elapsed_seconds, bool)
            or not isinstance(self.elapsed_seconds, (int, float))
            or not is_finite_number(self.elapsed_seconds)
            or self.elapsed_seconds < 0
        ):
            raise ValueError("Provider resolution elapsed time must be finite and nonnegative.")
        if self.error_type is not None and (
            not isinstance(self.error_type, str)
            or not 1 <= len(self.error_type) <= 127
            or any(ord(character) < 32 or ord(character) == 127 for character in self.error_type)
        ):
            raise ValueError("Provider resolution error types must be bounded printable text.")
        object.__setattr__(self, "elapsed_seconds", float(self.elapsed_seconds))


ProviderObserver = Callable[[ProviderResolution], None]


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
        if (
            isinstance(cleanup_timeout, bool)
            or not isinstance(cleanup_timeout, (int, float))
            or not is_finite_number(cleanup_timeout)
            or cleanup_timeout <= 0
        ):
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
        self._observers: list[ProviderObserver] = []
        self._detached_exits: set[asyncio.Future[Any]] = set()

    @property
    def providers(self) -> dict[DependencyKey, Provider]:
        """Return a copy of provider definitions for diagnostics."""
        return dict(self._providers)

    @property
    def dependency_graph(self) -> dict[DependencyKey, tuple[DependencyKey, ...]]:
        """Return a detached provider dependency graph for diagnostics and tooling."""
        return {key: provider.dependencies for key, provider in self._providers.items()}

    def freeze(self) -> None:
        """Validate the graph and prohibit changes during runtime."""
        self.validate()
        self._root._frozen = True

    def observe(self, observer: ProviderObserver) -> None:
        """Register a payload-free provider resolution observer on the root container."""
        if not callable(observer):
            raise TypeError("Provider observer must be callable.")
        if len(self._root._observers) >= _MAX_CORE_CAPACITY:
            raise RuntimeError("Provider observer capacity reached.")
        self._root._observers.append(observer)

    def _notify(self, resolution: ProviderResolution) -> None:
        for observer in tuple(self._root._observers):
            try:
                observer(resolution)
            except Exception:
                _LOG.exception("Provider observer failed", extra={"key": str(resolution.key)})

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
        self._validate_registration(key, factory, scope, dependencies, replace)
        self._register(key, Provider(factory, scope, dependencies), replace)

    def register_alias(
        self,
        alias: DependencyKey,
        target: DependencyKey,
        *,
        replace: bool = False,
    ) -> None:
        """Register a name that resolves the target provider without duplicating its value.

        Aliases preserve the target lifetime and resolve through the normal dependency graph.
        They are represented as async factories so aliases work for both synchronous and
        asynchronous target providers; use ``aresolve`` when resolving an alias.
        """
        self._validate_key(alias)
        self._validate_key(target)
        if not isinstance(replace, bool):
            raise TypeError("replace must be a boolean.")
        target_provider = self._providers.get(target)
        scope = target_provider.scope if target_provider is not None else Scope.SINGLETON

        async def resolve_target(container: Container) -> Any:
            """Resolve the aliased target through the caller's active scope."""
            return await container.aresolve(target)

        self._register(alias, Provider(resolve_target, scope, (target,)), replace)

    def register_resource(
        self,
        key: DependencyKey,
        factory: Callable[[Container], Any],
        *,
        scope: Scope = Scope.SINGLETON,
        dependencies: tuple[DependencyKey, ...] = (),
    ) -> None:
        """Register a sync/async context manager, entered by aresolve and exited at scope end."""
        self._validate_registration(key, factory, scope, dependencies, False)
        self._register(key, Provider(factory, scope, dependencies, resource=True), False)

    def _register(self, key: DependencyKey, provider: Provider, replace: bool) -> None:
        self._validate_key(key)
        self._ensure_open()
        if self is not self._root or self._root._frozen:
            raise _error("frozen", "Provider registration is closed.")
        if key not in self._providers and len(self._providers) >= _MAX_CORE_CAPACITY:
            raise _error("capacity", "Provider registry capacity reached.")
        if key in self._providers and not replace:
            raise _error("duplicate-provider", f"A provider is already registered for {key}.")
        if key in self._cache and replace:
            raise _error("resolved-provider", "Cannot replace a resolved provider.")
        self._providers[key] = provider

    def validate(self) -> None:
        """Reject missing providers, cycles and singleton dependencies on scoped values.

        Validation uses an explicit depth-first stack rather than Python recursion. Provider
        graphs are bounded by Core cardinality limits, but a valid graph can still be deeper
        than the interpreter recursion limit; graph shape must not decide whether validation
        returns a structured ``ContainerError`` or leaks ``RecursionError``.
        """
        # ``state`` is a DFS color map. A separate finish order lets scope analysis reuse the
        # validated graph instead of traversing every root branch repeatedly.
        state: dict[DependencyKey, int] = {}
        finish_order: list[DependencyKey] = []
        path: list[DependencyKey] = []
        positions: dict[DependencyKey, int] = {}
        for root in self._providers:
            if state.get(root) == 2:
                continue
            # Each frame stores the provider key and the next dependency index to visit.
            stack: list[tuple[DependencyKey, int]] = [(root, 0)]
            while stack:
                key, dependency_index = stack[-1]
                if dependency_index == 0:
                    current_state = state.get(key, 0)
                    if current_state == 2:
                        stack.pop()
                        continue
                    if current_state == 1:
                        cycle = tuple(path[positions[key] :] + [key])
                        raise _error("circular-dependency", f"Circular dependency: {cycle}")
                    provider = self._providers.get(key)
                    if provider is None:
                        raise _error("provider-not-found", f"No provider registered for {key}.")
                    state[key] = 1
                    positions[key] = len(path)
                    path.append(key)

                provider = self._providers[key]
                if dependency_index >= len(provider.dependencies):
                    state[key] = 2
                    finish_order.append(key)
                    positions.pop(key, None)
                    path.pop()
                    stack.pop()
                    continue
                dependency = provider.dependencies[dependency_index]
                stack[-1] = (key, dependency_index + 1)
                if state.get(dependency, 0) == 1:
                    cycle = tuple(path[positions[dependency] :] + [dependency])
                    raise _error("circular-dependency", f"Circular dependency: {cycle}")
                if state.get(dependency, 0) == 0:
                    stack.append((dependency, 0))

        scoped_dependency: dict[DependencyKey, DependencyKey | None] = {}
        for key in finish_order:
            provider = self._providers[key]
            offender: DependencyKey | None = key if provider.scope is Scope.SCOPED else None
            if offender is None:
                for dependency in provider.dependencies:
                    offender = scoped_dependency[dependency]
                    if offender is not None:
                        break
            scoped_dependency[key] = offender
        for _key, provider in self._providers.items():
            if provider.scope is Scope.SINGLETON:
                offender = next(
                    (
                        scoped_dependency[dependency]
                        for dependency in provider.dependencies
                        if scoped_dependency[dependency] is not None
                    ),
                    None,
                )
                if offender is not None:
                    raise _error(
                        "captive-dependency",
                        f"Singleton cannot depend on scoped {offender}.",
                    )

    def _ensure_open(self) -> None:
        if self._closed or self._root._closed or self._closing or self._root._closing:
            raise _error("closed", "The dependency scope is closed.")

    def _prepare(self, key: DependencyKey) -> tuple[Provider, Container]:
        self._validate_key(key)
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
    def resolve(self, key: type[T]) -> T:
        """Resolve a typed class key synchronously."""

    @overload
    def resolve(self, key: str) -> Any:
        """Resolve a named key synchronously."""

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
            started = monotonic()
            success = False
            error_type: str | None = None
            try:
                instance = provider.factory(owner)
                if inspect.isawaitable(instance):
                    if inspect.iscoroutine(instance):
                        instance.close()
                    raise _error("async-required", "Async factories require aresolve().")
                if provider.scope is not Scope.TRANSIENT:
                    owner._cache[key] = instance
                success = True
                return instance
            except BaseException as exc:
                error_type = type(exc).__name__
                raise
            finally:
                _path.reset(token)
                self._notify(
                    ProviderResolution(
                        key, provider.scope, success, monotonic() - started, error_type
                    )
                )

    @overload
    async def aresolve(self, key: type[T]) -> T:
        """Resolve a typed class key asynchronously."""

    @overload
    async def aresolve(self, key: str) -> Any:
        """Resolve a named key asynchronously."""

    async def aresolve(self, key: DependencyKey) -> Any:
        """Resolve a provider under its owning scope's construction transaction.

        Independent request scopes construct concurrently. Singleton construction uses
        the root lock. Nested resolution must be awaited directly: spawning a task from
        inside a factory is rejected rather than inheriting permission to bypass locks.
        """
        _, owner = self._prepare(key)
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Dependency resolution requires an active task.")
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
        started = monotonic()
        success = False
        error_type: str | None = None
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
            success = True
            return cast(object, instance)
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            owner._resolving.discard(key)
            _path.reset(token)
            self._notify(
                ProviderResolution(key, provider.scope, success, monotonic() - started, error_type)
            )

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
        self._validate_key(key)
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

    @staticmethod
    def _validate_key(key: DependencyKey) -> None:
        """Validate the type-or-string key used by every container boundary."""
        _validate_dependency_key(key)

    @classmethod
    def _validate_registration(
        cls,
        key: DependencyKey,
        factory: object,
        scope: Scope,
        dependencies: tuple[DependencyKey, ...],
        replace: bool,
    ) -> None:
        """Validate provider registration metadata before mutating the provider graph."""
        cls._validate_key(key)
        if not callable(factory):
            raise TypeError("Provider factories must be callable.")
        if not isinstance(scope, Scope):
            raise TypeError("Provider scope must be a Scope.")
        if not isinstance(dependencies, tuple):
            raise TypeError("Provider dependencies must be a tuple of keys.")
        if len(dependencies) > _MAX_RELATION_ENTRIES:
            raise ValueError(
                f"Provider dependencies cannot contain more than {_MAX_RELATION_ENTRIES:,} entries."
            )
        for dependency in dependencies:
            cls._validate_key(dependency)
        if not isinstance(replace, bool):
            raise TypeError("replace must be a boolean.")

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
        """Run one resource exit under a deadline without blocking later stack exits."""
        outcome: Any
        try:
            if asynchronous:
                outcome = manager.__aexit__(None, None, None)
            else:
                # A synchronous context manager may perform blocking I/O. Run its
                # exit hook outside the event loop and apply the same deadline as
                # asynchronous resources; a timed-out daemon thread cannot stall
                # application shutdown.
                outcome = _run_sync_exit(manager)
            task = asyncio.ensure_future(outcome)
            done, _ = await asyncio.wait({task}, timeout=self._cleanup_timeout)
            if not done:
                self._detach_exit(task)
                raise TimeoutError("Resource exit exceeded its cleanup deadline.")
            task.result()
        except (Exception, asyncio.CancelledError) as exc:
            if isinstance(exc, asyncio.CancelledError) and "task" in locals():
                self._detach_exit(task)
            self._cleanup_errors.append(
                exc if isinstance(exc, Exception) else RuntimeError("Resource exit cancelled.")
            )

    def _detach_exit(self, task: asyncio.Future[Any]) -> None:
        """Retain one cancellation-resistant resource exit until it actually finishes."""
        self._detached_exits.add(task)
        task.cancel()
        task.add_done_callback(self._retire_exit)

    def _retire_exit(self, task: asyncio.Future[Any]) -> None:
        """Forget a detached resource exit and consume late failures without warnings."""
        self._detached_exits.discard(task)
        _consume_future_result(task)


__all__ = ["Container", "ProviderObserver", "ProviderResolution"]


async def _run_sync_exit(manager: Any) -> None:
    """Run a blocking synchronous exit hook on a daemon thread with cancellation support."""
    loop = asyncio.get_running_loop()
    result: asyncio.Future[None] = loop.create_future()

    def complete(value: None = None, error: BaseException | None = None) -> None:
        """Resolve the event-loop future exactly once from the worker thread."""
        if result.done():
            return
        if error is None:
            result.set_result(value)
        else:
            result.set_exception(error)

    def invoke() -> None:
        """Call the blocking exit hook and transfer its result to the event loop."""
        try:
            manager.__exit__(None, None, None)
        except BaseException as exc:
            try:
                loop.call_soon_threadsafe(complete, None, exc)
            except RuntimeError:
                return
        else:
            try:
                loop.call_soon_threadsafe(complete)
            except RuntimeError:
                return

    Thread(target=invoke, name="orbit-container-exit", daemon=True).start()
    await result


def _consume_future_result(task: asyncio.Future[Any]) -> None:
    """Consume a detached resource-exit result without emitting loop-level warnings."""
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        return
