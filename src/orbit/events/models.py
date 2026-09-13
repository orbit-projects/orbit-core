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
"""Typed data exchanged through Orbit's in-process event bus."""

from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from orbit.types import EventId, PluginId, ServiceId, new_event_id

PayloadT = TypeVar("PayloadT")


class Event(BaseModel, Generic[PayloadT]):
    """An immutable event emitted by a service or plugin."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EventId = Field(default_factory=new_event_id)
    name: str = Field(pattern=r"^[a-z][a-z0-9.-]{0,126}$")
    payload: PayloadT
    producer: ServiceId | PluginId | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = ["Event", "PayloadT"]
