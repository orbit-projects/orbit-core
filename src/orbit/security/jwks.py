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
"""Provider-neutral contracts for rotating JSON Web Key Sets (JWKS)."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from orbit._immutability import freeze_mapping, validate_mapping
from orbit._limits import datetime_utc_microseconds


class JsonWebKey(BaseModel):
    """Public JWK metadata supplied by an authentication adapter."""

    model_config = ConfigDict(frozen=True, extra="allow", validate_default=True)

    key_id: StrictStr = Field(alias="kid", min_length=1, max_length=255)
    key_type: StrictStr = Field(alias="kty", min_length=1, max_length=32)
    algorithm: StrictStr | None = Field(default=None, alias="alg", min_length=1, max_length=32)
    use: StrictStr | None = Field(default="sig", min_length=1, max_length=16)

    @field_validator("key_id", "key_type", "algorithm", "use")
    @classmethod
    def validate_metadata_text(cls, value: str | None) -> str | None:
        """Reject control characters before key metadata reaches selection or diagnostics."""
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("JWK metadata cannot contain control characters.")
        return value

    def model_post_init(self, __context: object) -> None:
        """Validate and freeze provider-specific fields while retaining forward compatibility."""
        if self.model_extra:
            validate_mapping(
                self.model_extra,
                name="JWK provider metadata",
                max_entries=128,
                max_key_length=255,
            )
            object.__setattr__(self, "__pydantic_extra__", freeze_mapping(self.model_extra))


class JsonWebKeySet(BaseModel):
    """Immutable snapshot of keys fetched from an issuer's JWKS endpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    keys: tuple[JsonWebKey, ...] = Field(default=(), max_length=1_000)
    fetched_at: datetime
    expires_at: datetime

    @model_validator(mode="after")
    def validate_snapshot(self) -> JsonWebKeySet:
        """Require timezone-aware ordered timestamps and unique key identifiers."""
        if datetime_utc_microseconds(self.expires_at) <= datetime_utc_microseconds(self.fetched_at):
            raise ValueError("JWKS expiry must be after fetch time.")
        key_ids = [key.key_id for key in self.keys]
        if len(key_ids) != len(set(key_ids)):
            raise ValueError("JWKS key IDs must be unique.")
        return self

    def usable(self, key_id: str, *, now: datetime | None = None) -> JsonWebKey | None:
        """Return a key by ID while the snapshot is within its freshness window."""
        if (
            not isinstance(key_id, str)
            or not 1 <= len(key_id) <= 255
            or any(ord(character) < 32 or ord(character) == 127 for character in key_id)
        ):
            raise ValueError("JWKS key IDs must be nonempty strings of at most 255 characters.")
        current = datetime.now(UTC) if now is None else now
        if datetime_utc_microseconds(current) >= datetime_utc_microseconds(self.expires_at):
            return None
        return next((key for key in self.keys if key.key_id == key_id), None)


@runtime_checkable
class JwksProvider(Protocol):
    """Fetch a fresh key snapshot; adapters own transport and cryptography."""

    def get_keys(self) -> JsonWebKeySet | Awaitable[JsonWebKeySet]:
        """Return the currently published signing keys."""


__all__ = ["JsonWebKey", "JsonWebKeySet", "JwksProvider"]
