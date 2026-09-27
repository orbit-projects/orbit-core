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
"""Async transport contract for durable and cross-process event adapters."""

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from orbit.events.models import Event

EventHandler = Callable[[Event[Any]], Awaitable[None]]


@runtime_checkable
class EventTransport(Protocol):
    """Transport boundary with explicit subscription and shutdown ownership."""

    async def publish(self, event: Event[Any]) -> None:
        """Publish an event according to the adapter's durability contract."""

    async def subscribe(self, event_name: str, handler: EventHandler) -> object:
        """Subscribe a handler and return an adapter-owned subscription token."""

    async def unsubscribe(self, token: object) -> None:
        """Remove a subscription token."""

    async def close(self) -> None:
        """Stop consumers and release transport resources."""


__all__ = ["EventHandler", "EventTransport"]
