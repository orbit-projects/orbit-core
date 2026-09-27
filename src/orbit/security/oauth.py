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
"""Provider-neutral OAuth 2.0 and OpenID Connect contracts."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StrictInt, StrictStr, field_validator

from orbit._immutability import freeze_mapping, validate_mapping
from orbit.security.roles import validate_role_collection


class OAuthAuthorizationRequest(BaseModel):
    """Validated parameters for an OAuth authorization redirect."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    client_id: StrictStr = Field(min_length=1, max_length=255)
    redirect_uri: HttpUrl
    response_type: StrictStr = Field(default="code", pattern=r"^code$")
    scope: frozenset[StrictStr] = frozenset()
    state: StrictStr = Field(min_length=16, max_length=512)
    code_challenge: StrictStr | None = Field(default=None, min_length=43, max_length=128)
    code_challenge_method: StrictStr | None = Field(default=None, pattern=r"^(S256|plain)$")

    @field_validator("client_id", "state")
    @classmethod
    def validate_text(cls, value: str) -> str:
        """Reject control characters before OAuth values reach redirects or diagnostics."""
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("OAuth request text cannot contain control characters.")
        return value

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: frozenset[str]) -> frozenset[str]:
        """Keep requested scope identifiers bounded and free of ambiguous whitespace."""
        return validate_role_collection(value)

    @field_validator("code_challenge")
    @classmethod
    def validate_code_challenge(cls, value: str | None) -> str | None:
        """Require RFC 7636 base64url challenge text when PKCE is supplied."""
        if value is not None and not all(
            (character.isascii() and character.isalnum()) or character in "-._~"
            for character in value
        ):
            raise ValueError("code_challenge must use base64url characters.")
        return value

    @field_validator("redirect_uri")
    @classmethod
    def validate_redirect_uri(cls, value: HttpUrl) -> HttpUrl:
        """Require a secure callback transport, except for local development."""
        if not is_https_url(str(value)):
            raise ValueError(
                "redirect_uri must use HTTPS; HTTP callbacks are allowed only for localhost."
            )
        return value

    def model_post_init(self, __context: object) -> None:
        """Require PKCE consistency and reject the weak ``plain`` challenge method."""
        if self.code_challenge_method is not None and self.code_challenge is None:
            raise ValueError("code_challenge is required when a method is supplied.")
        if self.code_challenge_method == "plain":
            raise ValueError("Only S256 PKCE challenges are accepted.")


class OAuthTokenResponse(BaseModel):
    """Normalized token endpoint response; adapters must not expose client secrets."""

    model_config = ConfigDict(frozen=True, extra="allow", validate_default=True)

    access_token: StrictStr = Field(min_length=1, max_length=16_384)
    token_type: StrictStr = Field(default="Bearer", min_length=1, max_length=32)
    expires_in: StrictInt = Field(gt=0)
    refresh_token: StrictStr | None = Field(default=None, max_length=16_384)
    scope: frozenset[StrictStr] = frozenset()

    @field_validator("access_token", "token_type", "refresh_token")
    @classmethod
    def validate_token_text(cls, value: str | None) -> str | None:
        """Reject control characters before token responses reach logs or adapters."""
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("OAuth token values cannot contain control characters.")
        return value

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: frozenset[str]) -> frozenset[str]:
        """Keep granted OAuth scopes bounded before they reach authorization code."""
        return validate_role_collection(value)

    def model_post_init(self, __context: object) -> None:
        """Validate and freeze provider fields while retaining bounded forward-compatible extras."""
        if self.model_extra:
            validate_mapping(
                self.model_extra,
                name="OAuth provider metadata",
                max_entries=128,
                max_key_length=255,
            )
            object.__setattr__(self, "__pydantic_extra__", freeze_mapping(self.model_extra))


class OIDCDiscoveryDocument(BaseModel):
    """Subset of OpenID Connect discovery metadata required by Orbit adapters."""

    model_config = ConfigDict(frozen=True, extra="allow", validate_default=True)

    issuer: HttpUrl
    authorization_endpoint: HttpUrl
    token_endpoint: HttpUrl
    jwks_uri: HttpUrl
    response_types_supported: tuple[StrictStr, ...] = Field(default=(), max_length=128)
    subject_types_supported: tuple[StrictStr, ...] = Field(default=(), max_length=128)
    id_token_signing_alg_values_supported: tuple[StrictStr, ...] = Field(default=(), max_length=128)

    @field_validator("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri")
    @classmethod
    def validate_endpoint_transport(cls, value: HttpUrl) -> HttpUrl:
        """Require HTTPS provider endpoints, allowing HTTP only for localhost development."""
        if not is_https_url(str(value)):
            raise ValueError("OIDC endpoints must use HTTPS; only localhost HTTP is allowed.")
        return value

    @field_validator(
        "response_types_supported",
        "subject_types_supported",
        "id_token_signing_alg_values_supported",
    )
    @classmethod
    def validate_supported_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject ambiguous discovery capability names before adapters consume them."""
        if any(
            not 1 <= len(item) <= 255
            or any(ord(character) < 32 or ord(character) == 127 for character in item)
            for item in value
        ):
            raise ValueError("OIDC discovery capability values must be bounded printable text.")
        if len(value) != len(set(value)):
            raise ValueError("OIDC discovery capability values must be unique.")
        return value

    def model_post_init(self, __context: object) -> None:
        """Validate and freeze discovery fields while retaining bounded provider extras."""
        if self.model_extra:
            validate_mapping(
                self.model_extra,
                name="OIDC provider metadata",
                max_entries=128,
                max_key_length=255,
            )
            object.__setattr__(self, "__pydantic_extra__", freeze_mapping(self.model_extra))


@runtime_checkable
class OAuthProvider(Protocol):
    """OAuth adapter boundary; transport, credentials, and provider policy stay external."""

    def authorize_url(self, request: OAuthAuthorizationRequest) -> str:
        """Build an authorization URL from validated request parameters."""

    def exchange_code(
        self, code: str, *, redirect_uri: str, code_verifier: str | None = None
    ) -> OAuthTokenResponse | Awaitable[OAuthTokenResponse]:
        """Exchange a short-lived authorization code for tokens."""


@runtime_checkable
class OIDCDiscoveryProvider(Protocol):
    """Fetch and cache discovery metadata for an OIDC issuer."""

    def discover(self) -> OIDCDiscoveryDocument | Awaitable[OIDCDiscoveryDocument]:
        """Return validated discovery metadata."""


def is_https_url(value: str) -> bool:
    """Return whether a callback or provider URL uses HTTPS (localhost is allowed for dev)."""
    if not isinstance(value, str):
        return False
    if any(
        character.isspace() or character == "\\" or ord(character) < 0x20 or ord(character) == 0x7F
        for character in value
    ):
        return False
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        # Accessing ``port`` forces urllib to validate malformed or out-of-range ports.
        port = parsed.port
    except ValueError:
        return False
    if port is not None and not 0 <= port <= 65535:
        return False
    if (
        not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.netloc.endswith(":")
        or "%" in hostname
    ):
        return False
    if parsed.fragment:
        return False
    return parsed.scheme == "https" or (parsed.scheme == "http" and hostname == "localhost")


__all__ = [
    "OIDCDiscoveryDocument",
    "OIDCDiscoveryProvider",
    "OAuthAuthorizationRequest",
    "OAuthProvider",
    "OAuthTokenResponse",
    "is_https_url",
]
