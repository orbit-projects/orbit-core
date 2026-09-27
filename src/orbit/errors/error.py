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
"""Portable error model shared by exceptions, ASGI, and diagnostics."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from orbit._immutability import freeze_mapping, validate_mapping
from orbit.errors.categories import ErrorCategory, ErrorSeverity


class OrbitProblem(BaseModel):
    """A serializable representation of an operational failure."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    code: StrictStr = Field(pattern=r"^[a-z][a-z0-9.-]{0,126}$")
    message: StrictStr = Field(min_length=1, max_length=1024)
    category: ErrorCategory
    severity: ErrorSeverity = ErrorSeverity.ERROR
    context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    cause: StrictStr | None = Field(default=None, max_length=1024)

    @field_validator("message", "cause")
    @classmethod
    def validate_public_text(cls, value: str | None) -> str | None:
        """Keep reusable error text bounded and safe for logs and wire responses."""
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("Error text must be printable and cannot contain control characters.")
        return value

    @field_validator("context", mode="before")
    @classmethod
    def validate_context(cls, value: object) -> dict[str, Any]:
        """Bound error context before it reaches logs or responses."""
        return validate_mapping(value, name="Error context")

    @field_validator("metadata", mode="before")
    @classmethod
    def validate_metadata(cls, value: object) -> dict[str, Any]:
        """Bound error metadata before it reaches logs or responses."""
        return validate_mapping(value, name="Error metadata")

    def model_post_init(self, __context: object) -> None:
        """Freeze structured context so exceptions and diagnostics share stable errors."""
        object.__setattr__(self, "context", freeze_mapping(self.context))
        object.__setattr__(self, "metadata", freeze_mapping(self.metadata))


class ErrorResponse(BaseModel):
    """Safe HTTP error payload shared by runtime responses and OpenAPI."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    code: StrictStr = Field(pattern=r"^[a-z][a-z0-9.-]{0,126}$", max_length=127)
    message: StrictStr | None = Field(default=None, max_length=1024)
    request_id: StrictStr | None = Field(default=None, max_length=128)

    @field_validator("message", "request_id")
    @classmethod
    def validate_response_text(cls, value: str | None) -> str | None:
        """Keep HTTP error fields safe for JSON serialization and operator display."""
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("Error response text must be printable.")
        return value


__all__ = ["ErrorResponse", "OrbitProblem"]
