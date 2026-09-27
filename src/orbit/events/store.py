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
"""Durable event-store contracts with a bounded adapter-test implementation."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from orbit._limits import _MAX_CORE_CAPACITY, is_aware_datetime
from orbit.events.models import Event


@dataclass(frozen=True)
class StoredEvent:
    """An event plus its monotonic replay cursor assigned by a store.

    Store adapters cross a Core boundary here, so the result is validated even though it is a
    frozen dataclass. This keeps replay callers from receiving invalid cursors, event objects, or
    timezone-ambiguous retention timestamps.
    """

    sequence: int
    event: Event[Any]
    stored_at: datetime

    def __post_init__(self) -> None:
        """Validate adapter-owned replay metadata before it reaches application code."""
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError("Stored event sequences must be positive integers.")
        if not isinstance(self.event, Event):
            raise TypeError("Stored events must contain an Event instance.")
        if not is_aware_datetime(self.stored_at):
            raise ValueError("Stored event timestamps must include timezone information.")


@runtime_checkable
class EventStore(Protocol):
    """Append-only persistence boundary for durable delivery and replay adapters."""

    async def append(self, event: Event[Any]) -> int:
        """Persist an event and return its store-local monotonic sequence."""

    async def read(
        self, *, after: int = 0, event_name: str | None = None, limit: int = 100
    ) -> tuple[StoredEvent, ...]:
        """Read at most ``limit`` events after a cursor, optionally filtered by name."""

    async def prune_before(self, sequence: int) -> int:
        """Delete events with sequence lower than ``sequence`` and return the count removed."""

    async def close(self) -> None:
        """Flush and release store resources."""


class InMemoryEventStore:
    """Process-local bounded store for replay and adapter contract tests.

    Sequence numbers are monotonic only within this instance. The store provides no durability,
    replication, cross-process ordering, or crash recovery; a production event-store plugin must
    implement and document those properties.
    """

    def __init__(self, *, max_events: int = 10_000) -> None:
        if (
            isinstance(max_events, bool)
            or not isinstance(max_events, int)
            or not 1 <= max_events <= _MAX_CORE_CAPACITY
        ):
            raise ValueError("max_events must be positive and no more than 1,000,000.")
        self._max_events = max_events
        self._events: list[StoredEvent] = []
        self._next_sequence = 1
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def size(self) -> int:
        """Return the current number of retained events."""
        return len(self._events)

    async def append(self, event: Event[Any]) -> int:
        """Append an event and return its store-local replay sequence."""
        if not isinstance(event, Event):
            raise TypeError("Event store append requires an Event instance.")
        if self._closed:
            raise RuntimeError("Event store is closed.")
        async with self._lock:
            if self._closed:
                raise RuntimeError("Event store is closed.")
            if len(self._events) >= self._max_events:
                raise RuntimeError(
                    "Event store capacity reached; prune or provision more capacity."
                )
            # Retain a detached snapshot so callers cannot mutate the store after append returns.
            detached = event.model_copy(deep=True)
            sequence = self._next_sequence
            self._events.append(StoredEvent(sequence, detached, datetime.now(UTC)))
            self._next_sequence += 1
            return sequence

    async def read(
        self, *, after: int = 0, event_name: str | None = None, limit: int = 100
    ) -> tuple[StoredEvent, ...]:
        """Read bounded detached copies after a cursor, optionally filtering by event name."""
        if (
            isinstance(after, bool)
            or not isinstance(after, int)
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or after < 0
            or limit < 1
            or limit > 10_000
        ):
            raise ValueError("after must be nonnegative and limit must be between 1 and 10000.")
        if event_name is not None and (
            not isinstance(event_name, str)
            or not re.fullmatch(r"[a-z][a-z0-9.-]{0,126}", event_name)
        ):
            raise ValueError("event_name must be a valid event name.")
        async with self._lock:
            if self._closed:
                raise RuntimeError("Event store is closed.")
            selected = [
                item
                for item in self._events
                if item.sequence > after and (event_name is None or item.event.name == event_name)
            ][:limit]
            return tuple(
                StoredEvent(item.sequence, item.event.model_copy(deep=True), item.stored_at)
                for item in selected
            )

    async def prune_before(self, sequence: int) -> int:
        """Remove retained events older than ``sequence`` and return the removal count."""
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ValueError("sequence must be positive.")
        async with self._lock:
            if self._closed:
                raise RuntimeError("Event store is closed.")
            original = len(self._events)
            self._events = [item for item in self._events if item.sequence >= sequence]
            return original - len(self._events)

    async def close(self) -> None:
        """Mark the store closed so later reads and writes fail deterministically."""
        async with self._lock:
            self._closed = True


__all__ = ["EventStore", "InMemoryEventStore", "StoredEvent"]
