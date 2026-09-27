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

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from orbit._immutability import freeze_mapping, validate_mapping
from orbit._limits import is_aware_datetime
from orbit.types import EventId, PluginId, ServiceId, new_event_id

PayloadT = TypeVar("PayloadT")


class Event(BaseModel, Generic[PayloadT]):
    """An event envelope emitted by a service or Core extension.

    The Pydantic model and metadata mapping are protected after validation. Payload values remain
    application-owned objects, so delivery and storage boundaries still make deep copies when they
    retain or hand an event to another component. ``EventBus`` and the in-memory event store do so
    to prevent one subscriber or caller from mutating another component's view of an event.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    id: EventId = Field(default_factory=new_event_id)
    name: StrictStr = Field(pattern=r"^[a-z][a-z0-9.-]{0,126}$")
    payload: PayloadT
    producer: ServiceId | PluginId | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    delivery_key: StrictStr | None = Field(default=None, min_length=1, max_length=255)
    priority: StrictInt = Field(default=50, ge=0, le=100)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: object) -> dict[str, Any]:
        """Bound event metadata before it is retained by the event bus."""
        return validate_mapping(value, name="Event metadata")

    @field_validator("delivery_key")
    @classmethod
    def validate_delivery_key(cls, value: str | None) -> str | None:
        """Keep deduplication identity bounded and safe for diagnostics and adapters."""
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("Event delivery keys must be printable text.")
        return value

    @model_validator(mode="after")
    def validate_timestamp(self) -> Event[PayloadT]:
        """Require an aware occurrence time so event chronology is portable across processes."""
        if not is_aware_datetime(self.occurred_at):
            raise ValueError("Event timestamps must include timezone information.")
        return self

    def model_post_init(self, __context: object) -> None:
        """Freeze metadata so the event envelope stays stable after publication."""
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


__all__ = ["Event", "PayloadT"]
