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
"""Typed service identity and dependency metadata."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from orbit._immutability import freeze_mapping, validate_mapping
from orbit._limits import _MAX_RELATION_ENTRIES
from orbit.types import ServiceId, new_service_id


class ServiceDescriptor(BaseModel):
    """Immutable metadata used to register and order a service."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    id: ServiceId = Field(default_factory=new_service_id)
    name: StrictStr = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    version: StrictStr = Field(
        default="0.1.0",
        max_length=255,
        pattern=r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$",
    )
    dependencies: tuple[ServiceId, ...] = ()
    capabilities: frozenset[StrictStr] = frozenset()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, values: frozenset[str]) -> frozenset[str]:
        """Require bounded capability identifiers before service inspection or discovery."""
        if len(values) > 1_024 or any(
            not value
            or len(value) > 63
            or not value[0].islower()
            or any(
                not (character.isascii() and (character.isalnum() or character in "_.-"))
                for character in value
            )
            for value in values
        ):
            raise ValueError("Service capabilities must be bounded lowercase identifiers.")
        return values

    @field_validator("dependencies")
    @classmethod
    def validate_dependencies(cls, values: tuple[ServiceId, ...]) -> tuple[ServiceId, ...]:
        """Bound one service's dependency edges before graph validation retains them."""
        if len(values) > _MAX_RELATION_ENTRIES:
            raise ValueError(
                f"Service dependencies cannot contain more than {_MAX_RELATION_ENTRIES:,} entries."
            )
        return values

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: object) -> dict[str, Any]:
        """Bound service metadata before it is exposed through inspection."""
        return validate_mapping(value, name="Service metadata")

    def model_post_init(self, __context: object) -> None:
        """Freeze arbitrary metadata while retaining JSON-compatible serialization."""
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


__all__ = ["ServiceDescriptor"]
