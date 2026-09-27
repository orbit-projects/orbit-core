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
"""Tests for append-only event persistence and replay cursors."""

from datetime import UTC, datetime

import pytest

from orbit.events import Event, InMemoryEventStore, StoredEvent


def test_stored_event_validates_adapter_metadata() -> None:
    """Replay results cannot carry invalid cursors, event objects, or timestamps."""
    event = Event(name="stored", payload=None)
    timestamp = datetime.now(UTC)
    assert StoredEvent(1, event, timestamp).sequence == 1
    with pytest.raises(ValueError, match="positive"):
        StoredEvent(0, event, timestamp)
    with pytest.raises(TypeError, match="Event instance"):
        StoredEvent(1, object(), timestamp)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone"):
        StoredEvent(1, event, datetime(2026, 1, 1))


@pytest.mark.asyncio
async def test_event_store_assigns_cursors_and_replays_after_cursor() -> None:
    store = InMemoryEventStore(max_events=3)
    first = Event(name="user.created", payload={"id": 1})
    second = Event(name="order.created", payload={"id": 2})
    cursor = await store.append(first)
    await store.append(second)
    replay = await store.read(after=cursor)
    assert [item.event.name for item in replay] == ["order.created"]
    assert replay[0].sequence == 2
    assert (await store.read(event_name="user.created"))[0].event.id == first.id
    with pytest.raises(ValueError):
        await store.read(event_name="invalid name")
    with pytest.raises(ValueError):
        await store.read(after=True)


@pytest.mark.asyncio
async def test_event_store_replay_returns_detached_payloads() -> None:
    store = InMemoryEventStore()
    await store.append(Event(name="detached", payload={"nested": {"value": 1}}))
    replay = await store.read()
    replay[0].event.payload["nested"]["value"] = 99
    replay_again = await store.read()
    assert replay_again[0].event.payload["nested"]["value"] == 1


@pytest.mark.asyncio
async def test_event_store_detaches_payload_when_appending() -> None:
    store = InMemoryEventStore()
    payload = {"nested": {"value": 1}}
    event = Event(name="detached", payload=payload)

    await store.append(event)
    payload["nested"]["value"] = 99

    replay = await store.read()
    assert replay[0].event.payload["nested"]["value"] == 1


@pytest.mark.asyncio
async def test_event_store_copy_failure_does_not_consume_sequence() -> None:
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise RuntimeError("cannot copy")

    store = InMemoryEventStore()
    with pytest.raises(RuntimeError, match="cannot copy"):
        await store.append(Event(name="uncopyable", payload=Uncopyable()))
    assert store.size == 0
    assert await store.append(Event(name="after-failure", payload=None)) == 1


@pytest.mark.asyncio
async def test_event_store_rejects_non_event_append_values() -> None:
    with pytest.raises(TypeError, match="Event instance"):
        await InMemoryEventStore().append(object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_event_store_read_detaches_only_the_bounded_result_window() -> None:
    class FailsOnSecondCopy:
        def __init__(self):
            self.calls = 0

        def __deepcopy__(self, memo):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("late event copy failed")
            return self

    store = InMemoryEventStore()
    first = Event(name="first", payload=None)
    await store.append(first)
    await store.append(Event(name="late", payload=FailsOnSecondCopy()))

    replay = await store.read(limit=1)
    assert len(replay) == 1
    assert replay[0].event.id == first.id


@pytest.mark.asyncio
async def test_event_store_enforces_capacity_and_pruning() -> None:
    store = InMemoryEventStore(max_events=1)
    await store.append(Event(name="one", payload=None))
    with pytest.raises(RuntimeError, match="capacity"):
        await store.append(Event(name="two", payload=None))
    assert await store.prune_before(2) == 1
    assert store.size == 0
    await store.close()
    with pytest.raises(RuntimeError, match="closed"):
        await store.append(Event(name="three", payload=None))
    with pytest.raises(RuntimeError, match="closed"):
        await store.read()
    with pytest.raises(RuntimeError, match="closed"):
        await store.prune_before(1)
    with pytest.raises(ValueError, match="positive"):
        await InMemoryEventStore().prune_before(True)
    with pytest.raises(ValueError, match="positive"):
        await InMemoryEventStore().prune_before("1")  # type: ignore[arg-type]


def test_event_store_rejects_boolean_capacity() -> None:
    with pytest.raises(ValueError, match="positive"):
        InMemoryEventStore(max_events=True)
    with pytest.raises(ValueError, match="positive"):
        InMemoryEventStore(max_events="1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="1,000,000"):
        InMemoryEventStore(max_events=1_000_001)
