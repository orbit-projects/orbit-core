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
"""Provider-neutral secret references, resolution and rotation contracts."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretBytes,
    SecretStr,
    StrictStr,
    field_validator,
)

from orbit._limits import is_aware_datetime


class SecretReference(BaseModel):
    """An opaque backend reference that is safe to retain in configuration snapshots."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    uri: StrictStr = Field(pattern=r"^[a-z][a-z0-9+.-]*://[^\s]+$", max_length=2048)
    version: StrictStr | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("uri", "version")
    @classmethod
    def validate_reference_text(cls, value: str | None) -> str | None:
        """Reject control characters before references enter redacted configuration snapshots."""
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("Secret references and versions must be printable text.")
        return value

    def model_post_init(self, __context: object) -> None:
        """Reject malformed secret references before they can enter configuration state."""
        try:
            parsed = urlparse(self.uri)
            # Accessing ``port`` makes urllib validate malformed or out-of-range
            # numeric ports while keeping the URI opaque to Core's providers.
            hostname, port = parsed.hostname, parsed.port
        except ValueError as exc:
            raise ValueError("Secret references must contain a valid host and port.") from exc
        if (
            not hostname
            or parsed.username is not None
            or parsed.password is not None
            or "\\" in self.uri
            or parsed.netloc.endswith(":")
            or "%" in hostname
            or (port is not None and not 0 <= port <= 65_535)
        ):
            raise ValueError("Secret references must not contain credentials and need a host.")


class SecretValue(BaseModel):
    """Resolved secret value whose serialization and representation are always redacted."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    value: SecretStr | SecretBytes
    version: StrictStr = Field(min_length=1, max_length=255)
    resolved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("version")
    @classmethod
    def validate_version_text(cls, value: str) -> str:
        """Keep the retained secret version safe for operator-facing metadata."""
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Secret versions must be printable text.")
        return value

    def model_post_init(self, __context: object) -> None:
        """Require an aware resolution timestamp for cross-process secret audit records."""
        if not is_aware_datetime(self.resolved_at):
            raise ValueError("Secret resolution timestamps must include timezone information.")

    def reveal(self) -> str | bytes:
        """Explicitly access the secret; callers must avoid logging or persisting the result."""
        return self.value.get_secret_value()

    def model_dump(self, *args: object, **kwargs: object) -> dict[str, object]:
        """Return metadata only so accidental inspection cannot expose the secret."""
        return {"version": self.version, "resolved_at": self.resolved_at.isoformat()}


@runtime_checkable
class SecretManager(Protocol):
    """Resolve and rotate references without imposing a backend or network dependency."""

    def resolve(self, reference: SecretReference) -> SecretValue | Awaitable[SecretValue]:
        """Resolve the requested version, or the backend's current version when omitted."""

    def rotate(self, reference: SecretReference) -> SecretValue | Awaitable[SecretValue]:
        """Return the newly active version after a backend-managed rotation."""


__all__ = ["SecretManager", "SecretReference", "SecretValue"]
