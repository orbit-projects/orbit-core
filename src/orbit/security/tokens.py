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
"""Provider-neutral token lifecycle and revocation contracts."""

from __future__ import annotations

import heapq
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

from orbit._immutability import freeze_mapping
from orbit._limits import datetime_utc_microseconds
from orbit.security.claims import validate_claims
from orbit.security.roles import validate_role_collection

_MAX_REVOCATION_ENTRIES = 1_000_000


class Token(BaseModel):
    """Verified token metadata; raw credentials are intentionally not retained."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    token_id: StrictStr = Field(min_length=1, max_length=255)
    subject: StrictStr = Field(min_length=1, max_length=255)
    token_type: StrictStr = Field(default="access", pattern=r"^[a-z][a-z0-9-]{0,31}$")
    issued_at: datetime
    expires_at: datetime
    scopes: frozenset[StrictStr] = frozenset()
    claims: dict[str, Any] = Field(default_factory=dict)

    @field_validator("token_id", "subject")
    @classmethod
    def validate_identifier_text(cls, value: str) -> str:
        """Reject control characters before verified identity data reaches diagnostics."""
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Token identifiers and subjects cannot contain control characters.")
        return value

    @field_validator("token_type")
    @classmethod
    def validate_token_type_text(cls, value: str) -> str:
        """Reject control characters before token-type metadata reaches policy logic."""
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Token types cannot contain control characters.")
        return value

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, value: frozenset[str]) -> frozenset[str]:
        """Reject unsafe scope text before scopes become principal roles."""
        return validate_role_collection(value)

    @field_validator("claims", mode="before")
    @classmethod
    def validate_claim_mapping(cls, value: object) -> dict[str, Any]:
        """Bound provider claim keys before verified token metadata is retained."""
        return validate_claims(value)

    def model_post_init(self, __context: object) -> None:
        """Detach and freeze provider claims after Pydantic validation.

        ``frozen=True`` protects model attributes but does not protect a
        mutable dictionary stored in one of those attributes. Token claims
        are security-sensitive input and must remain stable after a verifier
        has returned the token.
        """
        object.__setattr__(self, "claims", freeze_mapping(self.claims))

    @model_validator(mode="after")
    def validate_window(self) -> Token:
        """Require timezone-aware timestamps with a strictly positive validity window."""
        if datetime_utc_microseconds(self.expires_at) <= datetime_utc_microseconds(self.issued_at):
            raise ValueError("Token expiry must be after issuance.")
        return self

    def is_expired(self, now: datetime | None = None) -> bool:
        """Return whether the token has reached its expiry instant."""
        current = datetime.now(UTC) if now is None else now
        return datetime_utc_microseconds(current) >= datetime_utc_microseconds(self.expires_at)


class TokenRevocationStore:
    """Thread-safe bounded revocation index keyed by token ID and expiry."""

    def __init__(self, *, max_entries: int = 100_000) -> None:
        if (
            isinstance(max_entries, bool)
            or not isinstance(max_entries, int)
            or not 1 <= max_entries <= _MAX_REVOCATION_ENTRIES
        ):
            raise ValueError("max_entries must be a positive integer no greater than 1,000,000.")
        self._max_entries = max_entries
        self._revoked: dict[str, int] = {}
        self._expiry_heap: list[tuple[int, str]] = []
        self._lock = RLock()

    def revoke(self, token: Token, *, now: datetime | None = None) -> bool:
        """Revoke a token until expiry; return whether this call added a new entry."""
        if not isinstance(token, Token):
            raise TypeError("Token revocation requires a Token instance.")
        current = datetime.now(UTC) if now is None else now
        with self._lock:
            current_key = datetime_utc_microseconds(current)
            self._purge(current_key)
            if token.is_expired(current):
                return False
            if token.token_id in self._revoked:
                return False
            if len(self._revoked) >= self._max_entries:
                raise RuntimeError("Token revocation capacity reached.")
            expiry_key = datetime_utc_microseconds(token.expires_at)
            self._revoked[token.token_id] = expiry_key
            heapq.heappush(self._expiry_heap, (expiry_key, token.token_id))
            return True

    def is_revoked(self, token_id: str, *, now: datetime | None = None) -> bool:
        """Return whether a non-expired revocation exists for the token ID."""
        if (
            not isinstance(token_id, str)
            or not 1 <= len(token_id) <= 255
            or any(ord(character) < 32 or ord(character) == 127 for character in token_id)
        ):
            raise ValueError("Token IDs must be nonempty and at most 255 characters.")
        current = datetime.now(UTC) if now is None else now
        with self._lock:
            self._purge(datetime_utc_microseconds(current))
            return token_id in self._revoked

    @property
    def size(self) -> int:
        """Return the current number of retained non-expired revocations."""
        with self._lock:
            self._purge(datetime_utc_microseconds(datetime.now(UTC)))
            return len(self._revoked)

    def _purge(self, now_key: int) -> None:
        """Remove only expired heap entries, avoiding a full scan on each token lookup."""
        while self._expiry_heap and self._expiry_heap[0][0] <= now_key:
            expires_key, token_id = heapq.heappop(self._expiry_heap)
            if self._revoked.get(token_id) == expires_key:
                del self._revoked[token_id]


__all__ = ["Token", "TokenRevocationStore"]
