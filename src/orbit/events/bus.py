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
"""Typed event delivery with unsubscribe, bounded diagnostics and failure aggregation."""

from __future__ import annotations

import asyncio
import inspect
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import UUID, uuid4

from orbit.events.models import Event

T = TypeVar("T")


@dataclass(frozen=True)
class Subscription:
    """Opaque handle used to remove one registration."""

    id: UUID
    name: str


@dataclass(frozen=True)
class Delivery:
    """Payload-free delivery record; diagnostic history never retains event secrets."""

    event_id: UUID
    name: str
    subscriber_count: int
    failures: int


class EventBus:
    """Deliver sequentially to a subscriber snapshot and attempt every subscriber.

    Handler failures are aggregated after delivery. Cancellation propagates immediately.
    This is an in-process bus: no persistence, retries or distributed delivery guarantees.
    """

    def __init__(self, *, timeout: float = 30, history_size: int = 100) -> None:
        self._subscribers: dict[
            UUID, tuple[str, Callable[[Event[Any]], Any], type[Any] | None]
        ] = {}
        self._history: deque[Delivery] = deque(maxlen=history_size)
        self._timeout = timeout
        self._closed = False

    @property
    def history(self) -> tuple[Delivery, ...]:
        """Return bounded delivery metadata without event payloads."""
        return tuple(self._history)

    def subscribe(
        self,
        event_name: str,
        subscriber: Callable[[Event[T]], Awaitable[None] | None],
        *,
        payload_type: type[T] | None = None,
    ) -> Subscription:
        """Register a handler with an optional runtime payload contract."""
        if self._closed:
            raise RuntimeError("Event bus is closed.")
        Event(name=event_name, payload=None)
        handle = Subscription(uuid4(), event_name)
        self._subscribers[handle.id] = (event_name, subscriber, payload_type)
        return handle

    def unsubscribe(self, subscription: Subscription) -> bool:
        """Remove a registration; return whether it existed."""
        return self._subscribers.pop(subscription.id, None) is not None

    async def publish(self, event: Event[Any]) -> None:
        """Deliver detached event copies and aggregate failures after all subscribers run."""
        if self._closed:
            raise RuntimeError("Event bus is closed.")
        subscribers = [item for item in self._subscribers.values() if item[0] == event.name]
        failures: list[Exception] = []
        for _, callback, payload_type in subscribers:
            try:
                if payload_type is not None and not isinstance(event.payload, payload_type):
                    raise TypeError(f"Event {event.name} requires {payload_type}.")
                result = callback(event.model_copy(deep=True))
                if inspect.isawaitable(result):
                    async with asyncio.timeout(self._timeout):
                        await result
            except Exception as exc:
                failures.append(exc)
        self._history.append(Delivery(event.id, event.name, len(subscribers), len(failures)))
        if failures:
            raise ExceptionGroup(f"Event {event.name} delivery failed", failures)

    def close(self) -> None:
        """Release subscriber references; retain only bounded delivery diagnostics."""
        self._closed = True
        self._subscribers.clear()


__all__ = ["Delivery", "EventBus", "Subscription"]
