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
"""Lease-based state coordination contracts."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from time import monotonic
from typing import Protocol, runtime_checkable

from orbit._limits import _MAX_CORE_CAPACITY, is_finite_number


@dataclass(frozen=True)
class Lease:
    """Opaque ownership token and monotonic expiry for one coordination key."""

    key: str
    token: str
    expires_at: float

    def __post_init__(self) -> None:
        """Validate and normalize ownership data before it crosses an adapter boundary."""
        for value, label in ((self.key, "Lease keys"), (self.token, "Lease tokens")):
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 255
                or any(
                    character.isspace() or ord(character) < 32 or ord(character) == 127
                    for character in value
                )
            ):
                raise ValueError(
                    f"{label} must be nonempty safe strings of at most 255 characters."
                )
        if (
            isinstance(self.expires_at, bool)
            or not isinstance(self.expires_at, (int, float))
            or not is_finite_number(self.expires_at)
        ):
            raise ValueError("Lease expiry must be a finite number.")
        object.__setattr__(self, "expires_at", float(self.expires_at))

    @property
    def expired(self) -> bool:
        """Return whether the lease's monotonic deadline has passed."""
        return monotonic() >= self.expires_at


@runtime_checkable
class StateCoordinator(Protocol):
    """Distributed-lock boundary with explicit lease ownership and renewal."""

    async def acquire(self, key: str, *, ttl: float = 30) -> Lease | None:
        """Acquire a key if free, returning an opaque lease or ``None`` when held."""

    async def renew(self, lease: Lease, *, ttl: float = 30) -> Lease | None:
        """Renew a still-owned lease, or return ``None`` when ownership was lost."""

    async def release(self, lease: Lease) -> bool:
        """Release only the exact owner token and report whether it was released."""

    async def close(self) -> None:
        """Release coordinator resources and reject future operations."""


class InMemoryStateCoordinator:
    """Single-process lease coordinator for tests and local development.

    This implementation provides the Core lease semantics but no cross-process visibility or
    durability. A distributed coordination plugin must implement the same ownership and renewal
    rules against its provider and document its consistency guarantees.
    """

    def __init__(self, *, max_keys: int = 10_000) -> None:
        if (
            isinstance(max_keys, bool)
            or not isinstance(max_keys, int)
            or not 1 <= max_keys <= _MAX_CORE_CAPACITY
        ):
            raise ValueError("max_keys must be positive and no more than 1,000,000.")
        self._max_keys = max_keys
        self._leases: dict[str, Lease] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def acquire(self, key: str, *, ttl: float = 30) -> Lease | None:
        """Atomically claim ``key`` until the returned lease expires."""
        self._validate(key, ttl)
        async with self._lock:
            self._ensure_open()
            self._purge_expired()
            current = self._leases.get(key)
            if current is not None and not current.expired:
                return None
            if current is None and len(self._leases) >= self._max_keys:
                raise RuntimeError("State coordinator key capacity reached.")
            lease = Lease(key, secrets.token_urlsafe(24), monotonic() + ttl)
            self._leases[key] = lease
            return lease

    async def renew(self, lease: Lease, *, ttl: float = 30) -> Lease | None:
        """Extend an unexpired lease only when its opaque token still owns the key."""
        if not isinstance(lease, Lease):
            raise TypeError("lease must be a Lease instance.")
        self._validate(lease.key, ttl)
        async with self._lock:
            self._ensure_open()
            # Renewal is also a capacity-maintenance operation. A coordinator that only
            # renews existing leases must not retain unrelated expired keys indefinitely.
            self._purge_expired()
            current = self._leases.get(lease.key)
            if current != lease or lease.expired:
                return None
            renewed = Lease(lease.key, lease.token, monotonic() + ttl)
            self._leases[lease.key] = renewed
            return renewed

    async def release(self, lease: Lease) -> bool:
        """Release a key only for the exact current lease owner."""
        if not isinstance(lease, Lease):
            raise TypeError("lease must be a Lease instance.")
        async with self._lock:
            self._ensure_open()
            # Release can be the only operation after a lease expires; reclaim stale keys here
            # as well so a subsequent acquire observes accurate capacity.
            self._purge_expired()
            current = self._leases.get(lease.key)
            if current != lease:
                return False
            del self._leases[lease.key]
            return True

    async def close(self) -> None:
        """Invalidate all local leases and reject subsequent coordination operations."""
        async with self._lock:
            self._closed = True
            self._leases.clear()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("State coordinator is closed.")

    def _purge_expired(self) -> None:
        """Remove expired leases before capacity accounting or ownership lookup."""
        for key, lease in tuple(self._leases.items()):
            if lease.expired:
                del self._leases[key]

    @staticmethod
    def _validate(key: str, ttl: float) -> None:
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 255
            or any(
                character.isspace() or ord(character) < 32 or ord(character) == 127
                for character in key
            )
        ):
            raise ValueError("Coordination keys must be nonempty and contain no whitespace.")
        if (
            isinstance(ttl, bool)
            or not isinstance(ttl, (int, float))
            or not is_finite_number(ttl)
            or ttl <= 0
        ):
            raise ValueError("Lease TTL must be positive.")


__all__ = ["InMemoryStateCoordinator", "Lease", "StateCoordinator"]
