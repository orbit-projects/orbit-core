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
"""Models used to report application and service health."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from orbit._immutability import freeze_mapping
from orbit._limits import is_aware_datetime

_MAX_DETAIL_ENTRIES = 2_048
_MAX_DETAIL_KEY_LENGTH = 255


class HealthStatus(StrEnum):
    """The current operational health of a component."""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class HealthReport(BaseModel):
    """A typed health result suitable for diagnostics or HTTP responses."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    status: HealthStatus = HealthStatus.UNKNOWN
    message: StrictStr | None = Field(default=None, max_length=1024)
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("details", mode="before")
    @classmethod
    def validate_details(cls, value: object) -> dict[str, Any]:
        """Bound operator-facing detail keys before they reach health or Admin responses."""
        if not isinstance(value, Mapping):
            raise TypeError("Health details must be a mapping.")
        # A custom Mapping can report an inaccurate length; enforce the bound while copying so
        # invalid health details cannot be materialized in full before rejection.
        details: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items(), start=1):
            if index > _MAX_DETAIL_ENTRIES:
                raise ValueError(f"Health details cannot exceed {_MAX_DETAIL_ENTRIES} entries.")
            if (
                not isinstance(key, str)
                or not 1 <= len(key) <= _MAX_DETAIL_KEY_LENGTH
                or any(ord(character) < 32 or ord(character) == 127 for character in key)
            ):
                raise ValueError("Health detail keys must be bounded printable strings.")
            details[key] = item
        return details

    @model_validator(mode="after")
    def validate_timestamp(self) -> HealthReport:
        """Require an aware timestamp so reports can be compared across processes."""
        # Treat a broken custom tzinfo like any other invalid report instead of leaking
        # an application exception from the datetime implementation.
        if not is_aware_datetime(self.checked_at):
            raise ValueError("Health report timestamps must include timezone information.")
        if self.message is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in self.message
        ):
            raise ValueError("Health report messages must be printable text.")
        return self

    def model_post_init(self, __context: object) -> None:
        """Freeze provider details so a recorded health report cannot be rewritten."""
        object.__setattr__(self, "details", freeze_mapping(self.details))


__all__ = ["HealthReport", "HealthStatus"]
