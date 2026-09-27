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
"""Bounded namespaced state with TTL and optimistic version checks."""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from time import monotonic
from typing import Any

from orbit._limits import _MAX_CORE_CAPACITY, is_finite_number

_STATE_KEY_PATTERN = re.compile(r"[a-zA-Z0-9_.:-]{1,255}")
_MAX_STATE_ENTRIES = 1_000_000


def _validate_state_key(key: str) -> str:
    """Validate one public namespace key before it reaches storage or inspection."""
    if not isinstance(key, str) or _STATE_KEY_PATTERN.fullmatch(key) is None:
        raise ValueError("State keys must be nonempty ASCII identifiers.")
    return key


def _validate_ttl(ttl: float | None) -> None:
    """Validate a positive finite retention interval before state allocation."""
    if ttl is not None and (
        isinstance(ttl, bool)
        or not isinstance(ttl, (int, float))
        or not is_finite_number(ttl)
        or ttl <= 0
    ):
        raise ValueError("ttl must be positive.")


def _validate_expected_version(expected_version: int | None) -> None:
    """Validate an optimistic-concurrency version without bool-as-int coercion."""
    if expected_version is not None and (
        isinstance(expected_version, bool)
        or not isinstance(expected_version, int)
        or expected_version < 0
    ):
        raise ValueError("expected_version must be a nonnegative integer.")


def _validate_write_inputs(key: str, ttl: float | None, expected_version: int | None) -> str:
    """Validate all state-write inputs before a provider creates a namespace."""
    _validate_ttl(ttl)
    _validate_expected_version(expected_version)
    return _validate_state_key(key)


@dataclass(frozen=True)
class StateEntry:
    """Validated, detached state value and its namespace revision.

    ``StateEntry`` is part of the public snapshot contract, so it protects callers that
    construct an entry directly as well as entries produced by :class:`StateNamespace`.
    Arbitrary state values are copied once at this boundary; namespace operations copy
    them again when storing or returning a snapshot.
    """

    key: str
    value: Any
    version: int
    expires_at: float | None = None

    def __post_init__(self) -> None:
        """Validate snapshot metadata and detach the retained value from its caller."""
        _validate_state_key(self.key)
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("State entry versions must be positive integers.")
        if self.expires_at is not None:
            if (
                isinstance(self.expires_at, bool)
                or not isinstance(self.expires_at, (int, float))
                or not is_finite_number(self.expires_at)
            ):
                raise ValueError("State entry expiry must be a finite number.")
            object.__setattr__(self, "expires_at", float(self.expires_at))
        object.__setattr__(self, "value", copy.deepcopy(self.value))


_DELETE = object()


class StateNamespace:
    """An isolated in-memory namespace suitable for ephemeral Core state and adapters."""

    def __init__(self, name: str, *, max_entries: int = 10_000) -> None:
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,62}", name):
            raise ValueError("Namespace names must be lowercase and use [a-z0-9_.-].")
        if (
            isinstance(max_entries, bool)
            or not isinstance(max_entries, int)
            or not 1 <= max_entries <= _MAX_STATE_ENTRIES
        ):
            raise ValueError("max_entries must be positive and no more than 1,000,000.")
        self._name = name
        self._max_entries = max_entries
        self._entries: dict[str, StateEntry] = {}
        self._revision = 0
        self._lock = RLock()

    @property
    def revision(self) -> int:
        """Return the latest namespace revision."""
        with self._lock:
            self._purge()
            return self._revision

    @property
    def name(self) -> str:
        """Return the immutable namespace identity."""
        return self._name

    def get(self, key: str, default: Any = None) -> Any:
        """Return a deep copy of a live value, or the supplied default."""
        with self._lock:
            self._purge()
            entry = self._entries.get(self._validate_key(key))
            return copy.deepcopy(default if entry is None else entry.value)

    def set(
        self,
        key: str,
        value: Any,
        *,
        ttl: float | None = None,
        expected_version: int | None = None,
    ) -> int:
        """Atomically store a value and return its new version."""
        key = _validate_write_inputs(key, ttl, expected_version)
        with self._lock:
            self._purge()
            if expected_version is not None:
                current = self._entries.get(key)
                actual = 0 if current is None else current.version
                if actual != expected_version:
                    raise ValueError("Stale state version.")
            if key not in self._entries and len(self._entries) >= self._max_entries:
                raise RuntimeError("State namespace capacity reached.")
            next_revision = self._revision + 1
            # Construct and detach the entry before publishing either the new revision or
            # mapping value. A provider copy failure must leave the namespace unchanged.
            entry = StateEntry(
                key,
                value,
                next_revision,
                None if ttl is None else monotonic() + ttl,
            )
            self._revision = next_revision
            self._entries[key] = entry
            return next_revision

    def delete(self, key: str, *, expected_version: int | None = None) -> bool:
        """Delete a key atomically and return whether an entry was removed."""
        key = self._validate_key(key)
        _validate_expected_version(expected_version)
        with self._lock:
            self._purge()
            current = self._entries.get(key)
            if expected_version is not None and (
                current is None or current.version != expected_version
            ):
                raise ValueError("Stale state version.")
            if current is None:
                return False
            del self._entries[key]
            self._revision += 1
            return True

    def snapshot(self) -> tuple[StateEntry, ...]:
        """Return detached live entries in stable key order."""
        with self._lock:
            self._purge()
            return tuple(copy.deepcopy(self._entries[key]) for key in sorted(self._entries))

    @contextmanager
    def transaction(self) -> Iterator[NamespaceTransaction]:
        """Stage multiple updates and commit them atomically at one namespace revision."""
        with self._lock:
            self._purge()
            transaction = NamespaceTransaction(self, self._revision)
        try:
            yield transaction
        except BaseException:
            transaction.rollback()
            raise
        else:
            transaction.commit()

    def _purge(self) -> None:
        now = monotonic()
        expired = False
        for key, entry in tuple(self._entries.items()):
            if entry.expires_at is not None and entry.expires_at <= now:
                del self._entries[key]
                expired = True
        if expired:
            # Expiration is an observable state mutation. Advancing the revision prevents
            # transactions created before cleanup from committing over a changed snapshot.
            self._revision += 1

    @staticmethod
    def _validate_key(key: str) -> str:
        return _validate_state_key(key)


class NamespaceTransaction:
    """Optimistic multi-key mutation for one :class:`StateNamespace`."""

    def __init__(self, namespace: StateNamespace, revision: int) -> None:
        self._namespace = namespace
        self._revision = revision
        self._changes: dict[str, tuple[Any, float | None] | object] = {}
        self._closed = False

    def get(self, key: str, default: Any = None) -> Any:
        """Read staged state first, then the namespace snapshot."""
        self._ensure_open()
        key = self._namespace._validate_key(key)
        change = self._changes.get(key)
        if change is _DELETE:
            return copy.deepcopy(default)
        if isinstance(change, tuple):
            return copy.deepcopy(change[0])
        return self._namespace.get(key, default)

    def set(self, key: str, value: Any, *, ttl: float | None = None) -> NamespaceTransaction:
        """Stage a value replacement with an optional positive TTL."""
        self._ensure_open()
        _validate_ttl(ttl)
        normalized_key = self._namespace._validate_key(key)
        if normalized_key not in self._changes and len(self._changes) >= _MAX_CORE_CAPACITY:
            raise RuntimeError("State transaction change capacity reached.")
        self._changes[normalized_key] = (
            copy.deepcopy(value),
            None if ttl is None else monotonic() + ttl,
        )
        return self

    def delete(self, key: str) -> NamespaceTransaction:
        """Stage deletion of a key; deleting a missing key is harmless."""
        self._ensure_open()
        normalized_key = self._namespace._validate_key(key)
        if normalized_key not in self._changes and len(self._changes) >= _MAX_CORE_CAPACITY:
            raise RuntimeError("State transaction change capacity reached.")
        self._changes[normalized_key] = _DELETE
        return self

    def commit(self) -> int:
        """Validate the base revision and atomically apply all staged changes."""
        self._ensure_open()
        namespace = self._namespace
        with namespace._lock:
            namespace._purge()
            if namespace._revision != self._revision:
                self._closed = True
                raise ValueError("Stale state namespace revision.")
            additions = sum(
                key not in namespace._entries and change is not _DELETE
                for key, change in self._changes.items()
            )
            if len(namespace._entries) + additions > namespace._max_entries:
                self._closed = True
                raise RuntimeError("State namespace capacity reached.")
            prepared: dict[str, tuple[Any, float | None] | object] = {}
            for key, change in self._changes.items():
                if change is _DELETE:
                    prepared[key] = _DELETE
                elif isinstance(change, tuple):
                    prepared[key] = (copy.deepcopy(change[0]), change[1])
            mutated = any(
                change is not _DELETE or key in namespace._entries
                for key, change in prepared.items()
            )
            if mutated:
                namespace._revision += 1
                revision = namespace._revision
                for key, change in prepared.items():
                    if change is _DELETE:
                        namespace._entries.pop(key, None)
                    elif isinstance(change, tuple):
                        value, expires_at = change
                        namespace._entries[key] = StateEntry(key, value, revision, expires_at)
            else:
                revision = namespace._revision
        self._closed = True
        return revision

    def rollback(self) -> None:
        """Discard staged changes without modifying the namespace."""
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("State transaction is closed.")


__all__ = ["NamespaceTransaction", "StateEntry", "StateNamespace"]
