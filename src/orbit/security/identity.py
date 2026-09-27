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
"""Provider-neutral authenticated identity model."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from orbit._immutability import freeze_mapping
from orbit.security.claims import validate_claims


class Identity(BaseModel):
    """A verified identity from an external authentication provider."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    subject: StrictStr = Field(min_length=1, max_length=255)
    provider: StrictStr = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    claims: dict[str, Any] = Field(default_factory=dict)

    @field_validator("subject")
    @classmethod
    def validate_subject_text(cls, value: str) -> str:
        """Reject control characters before a provider subject reaches audit records."""
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Identity subjects cannot contain control characters.")
        return value

    @field_validator("provider")
    @classmethod
    def validate_provider_text(cls, value: str) -> str:
        """Reject control characters before a provider identifier reaches audit records."""
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Identity providers cannot contain control characters.")
        return value

    @field_validator("claims", mode="before")
    @classmethod
    def validate_claim_mapping(cls, value: object) -> dict[str, Any]:
        """Bound provider claim keys before the verified identity is retained."""
        return validate_claims(value)

    def model_post_init(self, __context: object) -> None:
        """Detach and freeze claims so an authenticated identity is stable."""
        object.__setattr__(self, "claims", freeze_mapping(self.claims))


__all__ = ["Identity"]
