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
"""Async provider contract and deterministic in-memory adapter for state backends."""

import asyncio
import copy
import re
from typing import Any, Protocol, runtime_checkable

from orbit.state.namespace import (
    _MAX_STATE_ENTRIES,
    StateNamespace,
    _validate_expected_version,
    _validate_state_key,
    _validate_write_inputs,
)

_MAX_STATE_NAMESPACES = 1_000_000


@runtime_checkable
class StateProvider(Protocol):
    """Asynchronous backend contract for namespaced optimistic-versioned state.

    Implementations must define durability, consistency, serialization, capacity, and
    cross-process behavior. Core's in-memory implementation is intentionally process-local and
    exists for development and adapter contract tests.
    """

    async def get(self, namespace: str, key: str, default: Any = None) -> Any:
        """Return a detached namespaced value, or ``default`` when the key is absent."""

    async def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        *,
        ttl: float | None = None,
        expected_version: int | None = None,
    ) -> int:
        """Store a value, optionally enforcing a version precondition, and return its version."""

    async def delete(
        self, namespace: str, key: str, *, expected_version: int | None = None
    ) -> bool:
        """Delete a value subject to an optional version precondition."""

    async def close(self) -> None:
        """Release provider resources and reject subsequent operations."""


class InMemoryStateProvider:
    """Process-local async provider implementing Core state semantics for tests.

    Values are protected by one async lock; both namespace count and per-namespace entries are
    bounded. Missing-namespace reads do not allocate state. This class is not a durable store and
    must not be used as a substitute for a production state plugin.
    """

    def __init__(self, *, max_entries: int = 10_000, max_namespaces: int = 1_000) -> None:
        if (
            isinstance(max_entries, bool)
            or not isinstance(max_entries, int)
            or not 1 <= max_entries <= _MAX_STATE_ENTRIES
        ):
            raise ValueError("max_entries must be positive and no more than 1,000,000.")
        if (
            isinstance(max_namespaces, bool)
            or not isinstance(max_namespaces, int)
            or not 1 <= max_namespaces <= _MAX_STATE_NAMESPACES
        ):
            raise ValueError("max_namespaces must be positive and no more than 1,000,000.")
        self._max_entries = max_entries
        self._max_namespaces = max_namespaces
        self._namespaces: dict[str, StateNamespace] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def get(self, namespace: str, key: str, default: Any = None) -> Any:
        """Read a value under the provider lock and return ``default`` when absent."""
        # Validate before looking up the namespace so missing-namespace reads cannot bypass the
        # same key contract enforced by StateNamespace.
        _validate_state_key(key)
        async with self._lock:
            state = self._namespace(namespace, create=False)
            return copy.deepcopy(default) if state is None else state.get(key, default)

    async def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        *,
        ttl: float | None = None,
        expected_version: int | None = None,
    ) -> int:
        """Write a value with optional optimistic concurrency checking."""
        # Preflight before `_namespace()` so malformed writes cannot consume namespace slots.
        _validate_write_inputs(key, ttl, expected_version)
        async with self._lock:
            state = self._namespace(namespace)
            if state is None:
                raise RuntimeError("State provider failed to create a namespace.")
            return state.set(key, value, ttl=ttl, expected_version=expected_version)

    async def delete(
        self, namespace: str, key: str, *, expected_version: int | None = None
    ) -> bool:
        """Delete a value and report whether it existed and matched its version."""
        # Preflight both arguments before a missing namespace can turn malformed input into a
        # misleading no-op result.
        _validate_state_key(key)
        _validate_expected_version(expected_version)
        async with self._lock:
            state = self._namespace(namespace, create=False)
            return False if state is None else state.delete(key, expected_version=expected_version)

    async def close(self) -> None:
        """Close the provider, clear process-local state, and reject later operations."""
        async with self._lock:
            self._closed = True
            self._namespaces.clear()

    def _namespace(self, name: str, *, create: bool = True) -> StateNamespace | None:
        if self._closed:
            raise RuntimeError("State provider is closed.")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,62}", name):
            raise ValueError("Namespace names must be lowercase and use [a-z0-9_.-].")
        namespace = self._namespaces.get(name)
        if namespace is None:
            if not create:
                return None
            if len(self._namespaces) >= self._max_namespaces:
                raise RuntimeError("State provider namespace capacity reached.")
            namespace = StateNamespace(name, max_entries=self._max_entries)
            self._namespaces[name] = namespace
        return namespace


__all__ = ["InMemoryStateProvider", "StateProvider"]
