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
"""Reusable issuer, audience, and clock-skew validation for verified tokens."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictStr, field_validator

from orbit._limits import is_aware_datetime
from orbit.security.roles import validate_role_collection
from orbit.security.tokens import Token

_MAX_POLICY_TEXT = 255
_MAX_POLICY_ENTRIES = 1_024
_MAX_CLOCK_SKEW = timedelta(days=1)


def _normalize_audience_claim(value: object) -> frozenset[str]:
    """Validate and bound a verified token's audience claim before policy matching."""
    values: Iterable[object]
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        if len(value) > _MAX_POLICY_ENTRIES:
            raise ValueError("Token audience claim exceeds the configured cardinality limit.")
        values = value
    else:
        raise ValueError("Token audience claim is invalid.")
    normalized: set[str] = set()
    for item in values:
        if (
            not isinstance(item, str)
            or not 1 <= len(item) <= _MAX_POLICY_TEXT
            or any(ord(character) < 32 or ord(character) == 127 for character in item)
        ):
            raise ValueError("Token audience claim contains unsafe text.")
        normalized.add(item)
    return frozenset(normalized)


class TokenValidationPolicy(BaseModel):
    """Immutable provider-independent claim checks after signature verification.

    Pydantic supplies runtime validation and frozen configuration semantics. Keeping the
    policy immutable is important: changing issuer, audience, or required scopes after a
    verifier has been composed would silently change an application's trust boundary.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, validate_default=True)

    issuer: StrictStr | None = None
    audience: frozenset[StrictStr] = frozenset()
    clock_skew: timedelta = timedelta(seconds=30)
    required_scopes: frozenset[StrictStr] = frozenset()

    @field_validator("issuer")
    @classmethod
    def validate_issuer(cls, value: str | None) -> str | None:
        """Require an explicit nonempty issuer when issuer validation is enabled."""
        if value is not None and (
            not 1 <= len(value) <= _MAX_POLICY_TEXT
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("issuer must be bounded nonempty text.")
        return value

    @field_validator("audience", mode="before")
    @classmethod
    def normalize_audience(cls, value: object) -> frozenset[str]:
        """Normalize one audience string while rejecting mutable or ambiguous collections."""
        if value is None:
            return frozenset()
        if isinstance(value, str):
            values = frozenset({value})
        elif isinstance(value, frozenset):
            values = value
        else:
            raise ValueError("audience must be a string or frozenset of strings.")
        if len(values) > _MAX_POLICY_ENTRIES:
            raise ValueError("audience cannot contain more than 1,024 entries.")
        if any(
            not isinstance(item, str)
            or not 1 <= len(item) <= _MAX_POLICY_TEXT
            or any(ord(character) < 32 or ord(character) == 127 for character in item)
            for item in values
        ):
            raise ValueError("audience values must be bounded nonempty strings.")
        return values

    @field_validator("clock_skew")
    @classmethod
    def validate_clock_skew(cls, value: timedelta) -> timedelta:
        """Reject negative or excessively permissive validation tolerance."""
        if value < timedelta(0) or value > _MAX_CLOCK_SKEW:
            raise ValueError("clock_skew must be between zero and one day.")
        return value

    @field_validator("required_scopes", mode="before")
    @classmethod
    def validate_required_scopes(cls, value: object) -> frozenset[str]:
        """Require a frozen collection of nonempty scope names."""
        if not isinstance(value, frozenset):
            raise ValueError("required_scopes must be a frozenset of nonempty strings.")
        if len(value) > _MAX_POLICY_ENTRIES:
            raise ValueError("required_scopes cannot contain more than 1,024 entries.")
        try:
            return validate_role_collection(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("required_scopes must contain bounded scope identifiers.") from exc

    # The instance-level method intentionally keeps Orbit's public policy API; it shadows
    # Pydantic's class-level compatibility hook named ``validate``.
    def validate(self, token: Token, *, now: datetime | None = None) -> Token:  # type: ignore[override]
        """Return the token or raise ``ValueError`` when claims violate policy."""
        if not isinstance(token, Token):
            raise TypeError("Token validation requires a Token instance.")
        current = datetime.now(UTC) if now is None else now
        if not is_aware_datetime(current):
            raise ValueError("Token comparison timestamps must include timezone information.")
        try:
            expires_at = token.expires_at + self.clock_skew
            issued_at = token.issued_at - self.clock_skew
        except OverflowError as exc:
            raise ValueError("Token timestamps exceed the validation range.") from exc
        if expires_at <= current:
            raise ValueError("Token is expired.")
        if issued_at > current:
            raise ValueError("Token is not valid yet.")
        claims: dict[str, Any] = token.claims
        if self.issuer is not None and claims.get("iss") != self.issuer:
            raise ValueError("Token issuer is not trusted.")
        if self.audience:
            actual = _normalize_audience_claim(claims.get("aud", ()))
            if not self.audience.intersection(actual):
                raise ValueError("Token audience is not trusted.")
        if not self.required_scopes.issubset(token.scopes):
            raise ValueError("Token does not grant required scopes.")
        return token


__all__ = ["TokenValidationPolicy"]
