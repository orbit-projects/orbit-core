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
"""Tests for the async StateProvider reference implementation."""

import asyncio

import pytest

from orbit.state import InMemoryStateProvider


def test_provider_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        InMemoryStateProvider(max_entries=0)
    with pytest.raises(ValueError, match="positive"):
        InMemoryStateProvider(max_entries=True)
    with pytest.raises(ValueError, match="positive"):
        InMemoryStateProvider(max_entries="1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive"):
        InMemoryStateProvider(max_namespaces=0)
    with pytest.raises(ValueError, match="positive"):
        InMemoryStateProvider(max_namespaces=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="1,000,000"):
        InMemoryStateProvider(max_entries=1_000_001)
    with pytest.raises(ValueError, match="1,000,000"):
        InMemoryStateProvider(max_namespaces=1_000_001)


@pytest.mark.asyncio
async def test_provider_is_namespaced_and_copy_isolated() -> None:
    provider = InMemoryStateProvider()
    value = {"nested": [1]}
    version = await provider.set("sessions", "user", value, ttl=1)
    value["nested"].append(2)
    loaded = await provider.get("sessions", "user")
    assert loaded == {"nested": [1]}
    assert await provider.get("other", "user") is None
    with pytest.raises(ValueError, match="Stale"):
        await provider.set("sessions", "user", {}, expected_version=version + 1)
    assert await provider.delete("sessions", "user", expected_version=version)


@pytest.mark.asyncio
async def test_provider_ttl_and_close() -> None:
    provider = InMemoryStateProvider()
    await provider.set("cache", "key", "value", ttl=0.01)
    await asyncio.sleep(0.02)
    assert await provider.get("cache", "key", "fallback") == "fallback"
    await provider.close()
    with pytest.raises(RuntimeError, match="closed"):
        await provider.get("cache", "key")


@pytest.mark.asyncio
async def test_provider_bounds_namespace_creation_and_does_not_allocate_on_misses() -> None:
    provider = InMemoryStateProvider(max_namespaces=1)
    assert await provider.get("missing", "key", "fallback") == "fallback"
    assert await provider.delete("missing", "key") is False
    await provider.set("first", "key", "value")
    with pytest.raises(RuntimeError, match="namespace capacity"):
        await provider.set("second", "key", "value")


@pytest.mark.asyncio
async def test_provider_validates_writes_before_namespace_creation() -> None:
    """Malformed writes must not consume a namespace slot or bypass CAS typing."""
    provider = InMemoryStateProvider(max_namespaces=1)
    with pytest.raises(ValueError, match="State keys"):
        await provider.set("invalid", "bad key", "value")
    with pytest.raises(ValueError, match="expected_version"):
        await provider.set("invalid", "key", "value", expected_version=True)  # type: ignore[arg-type]
    await provider.set("valid", "key", "value")


@pytest.mark.asyncio
async def test_provider_validates_reads_and_deletes_before_missing_namespace_noops() -> None:
    """All provider operations enforce key and version contracts before namespace lookup."""
    provider = InMemoryStateProvider()
    with pytest.raises(ValueError, match="State keys"):
        await provider.get("missing", "bad key")
    with pytest.raises(ValueError, match="State keys"):
        await provider.delete("missing", "bad key")
    with pytest.raises(ValueError, match="expected_version"):
        await provider.delete("missing", "key", expected_version=True)  # type: ignore[arg-type]
