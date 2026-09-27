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
"""Administrative view models derived from Core state."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator

from orbit._limits import is_aware_datetime
from orbit.application.models import ApplicationSummary

AdminOverview = ApplicationSummary


class AdminAuditRecord(BaseModel):
    """Payload-free record of one authenticated administrative operation."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    id: UUID = Field(default_factory=uuid4)
    action: StrictStr = Field(pattern=r"^[a-z][a-z0-9.-]{0,62}$")
    target: StrictStr = Field(default="", max_length=255)
    subject: StrictStr = Field(default="anonymous", max_length=255)
    provider: StrictStr = Field(default="", max_length=63)
    success: StrictBool
    error_code: StrictStr | None = Field(
        default=None,
        max_length=127,
        pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,126}$",
    )
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("target", "subject", "provider")
    @classmethod
    def validate_text_fields(cls, value: str) -> str:
        """Reject control characters before text reaches operator-facing audit output."""
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Audit text fields cannot contain control characters.")
        return value

    @field_validator("occurred_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        """Keep audit chronology unambiguous across processes and deployment time zones."""
        if not is_aware_datetime(value):
            raise ValueError("Audit timestamps must include timezone information.")
        return value


__all__ = ["AdminAuditRecord", "AdminOverview"]
