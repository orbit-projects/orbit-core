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
"""Tests for lease ownership and expiry semantics."""

import asyncio

import pytest

from orbit.state import InMemoryStateCoordinator, Lease


@pytest.mark.asyncio
async def test_coordinator_enforces_exclusive_ownership_and_token_release() -> None:
    coordinator = InMemoryStateCoordinator()
    first = await coordinator.acquire("leader", ttl=1)
    assert first is not None
    assert await coordinator.acquire("leader", ttl=1) is None
    forged = first.__class__(first.key, "forged", first.expires_at)
    assert await coordinator.release(forged) is False
    assert await coordinator.release(first) is True
    second = await coordinator.acquire("leader", ttl=1)
    assert second is not None and second.token != first.token


@pytest.mark.asyncio
async def test_coordinator_bounds_key_cardinality_and_reclaims_expired_leases() -> None:
    coordinator = InMemoryStateCoordinator(max_keys=1)
    first = await coordinator.acquire("first", ttl=0.01)
    assert first is not None
    with pytest.raises(RuntimeError, match="capacity"):
        await coordinator.acquire("second", ttl=1)
    await asyncio.sleep(0.02)
    second = await coordinator.acquire("second", ttl=1)
    assert second is not None


@pytest.mark.asyncio
async def test_coordinator_rejects_invalid_key_capacity() -> None:
    with pytest.raises(ValueError, match="positive"):
        InMemoryStateCoordinator(max_keys=0)
    with pytest.raises(ValueError, match="positive"):
        InMemoryStateCoordinator(max_keys=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="1,000,000"):
        InMemoryStateCoordinator(max_keys=1_000_001)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ttl", [0, -1, True, "1", object(), float("inf"), float("-inf"), float("nan")]
)
async def test_coordinator_rejects_non_finite_or_non_positive_ttl(ttl: float) -> None:
    coordinator = InMemoryStateCoordinator()
    with pytest.raises(ValueError, match="TTL must be positive"):
        await coordinator.acquire("lease", ttl=ttl)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", [None, 42, object()])
async def test_coordinator_rejects_non_string_keys(key: object) -> None:
    coordinator = InMemoryStateCoordinator()
    with pytest.raises(ValueError, match="Coordination keys"):
        await coordinator.acquire(key)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_coordinator_renews_and_reclaims_expired_leases() -> None:
    coordinator = InMemoryStateCoordinator()
    lease = await coordinator.acquire("job", ttl=0.01)
    assert lease is not None
    renewed = await coordinator.renew(lease, ttl=1)
    assert renewed is not None and renewed.token == lease.token
    await asyncio.sleep(0.02)
    assert await coordinator.renew(lease, ttl=1) is None
    assert await coordinator.acquire("job", ttl=1) is None  # renewed lease is still active
    assert await coordinator.release(renewed) is True
    await coordinator.close()
    with pytest.raises(RuntimeError, match="closed"):
        await coordinator.acquire("other")


@pytest.mark.asyncio
async def test_coordinator_rejects_malformed_lease_inputs() -> None:
    coordinator = InMemoryStateCoordinator()
    for malformed in (None, object(), ("job", "token", 1)):
        with pytest.raises(TypeError, match="Lease"):
            await coordinator.renew(malformed)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="Lease"):
            await coordinator.release(malformed)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="safe strings"):
        Lease("job\n", "token", 1)
    with pytest.raises(ValueError, match="safe strings"):
        Lease("job", "", 1)
    with pytest.raises(ValueError, match="finite"):
        Lease("job", "token", float("nan"))
