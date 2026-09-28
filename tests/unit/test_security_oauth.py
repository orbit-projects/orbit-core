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
"""Tests for OAuth and OIDC adapter contracts."""

import pytest
from pydantic import ValidationError

from orbit.security import (
    OAuthAuthorizationRequest,
    OAuthTokenResponse,
    OIDCDiscoveryDocument,
    is_https_url,
)


def test_authorization_request_requires_strong_pkce() -> None:
    request = OAuthAuthorizationRequest(
        client_id="client",
        redirect_uri="https://example.test/callback",
        state="1234567890123456",
        code_challenge="a" * 43,
        code_challenge_method="S256",
    )
    assert request.response_type == "code"
    with pytest.raises(ValueError, match="Only S256"):
        OAuthAuthorizationRequest(
            client_id="client",
            redirect_uri="https://example.test/callback",
            state="1234567890123456",
            code_challenge="a" * 43,
            code_challenge_method="plain",
        )
    with pytest.raises(ValueError, match="code_challenge_method=S256"):
        OAuthAuthorizationRequest(
            client_id="client",
            redirect_uri="https://example.test/callback",
            state="1234567890123456",
            code_challenge="a" * 43,
        )
    with pytest.raises(ValidationError):
        OAuthAuthorizationRequest(
            client_id="client",
            redirect_uri="https://example.test/callback",
            state="1234567890123456",
            scope=frozenset({b"read"}),  # type: ignore[arg-type]
        )


def test_oauth_models_validate_urls_and_expiry() -> None:
    assert is_https_url("https://issuer.test")
    assert is_https_url("http://localhost:8000/callback")
    assert not is_https_url("http://issuer.test")
    assert not is_https_url("https://")
    with pytest.raises(ValidationError):
        OAuthTokenResponse(access_token="x", expires_in=0)


@pytest.mark.parametrize(
    "url",
    [
        "https://[::1",
        "https://issuer.test:not-a-port",
        "https://issuer.test:65536",
        "https://issuer.test:",
        "https://[::1]:",
        "https://[fe80::1%25eth0]",
        "https://issuer.test/unsafe path",
        "https://issuer.test/unsafe\\path",
        "https://issuer.test/unsafe\npath",
    ],
)
def test_https_url_helper_rejects_malformed_authority(url: str) -> None:
    """Direct URL policy checks fail closed instead of leaking parser exceptions."""
    assert not is_https_url(url)


def test_oauth_provider_extras_are_frozen_and_token_text_is_bounded() -> None:
    response = OAuthTokenResponse(access_token="x", expires_in=60, provider_data={"scopes": ["a"]})
    with pytest.raises(TypeError, match="immutable"):
        response.model_extra["provider_data"]["scopes"] = ["b"]
    with pytest.raises(ValidationError, match="control"):
        OAuthTokenResponse(access_token="bad\ntoken", expires_in=60)
    discovery = OIDCDiscoveryDocument(
        issuer="https://issuer.example",
        authorization_endpoint="https://issuer.example/authorize",
        token_endpoint="https://issuer.example/token",
        jwks_uri="https://issuer.example/jwks",
        provider_data={"features": ["pkce"]},
    )
    with pytest.raises(TypeError, match="immutable"):
        discovery.model_extra["provider_data"]["features"] = ["implicit"]
    with pytest.raises(ValidationError):
        OIDCDiscoveryDocument(
            issuer="https://issuer.example",
            authorization_endpoint="https://issuer.example/authorize",
            token_endpoint="https://issuer.example/token",
            jwks_uri="https://issuer.example/jwks",
            response_types_supported=(b"code",),  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        OAuthTokenResponse(
            access_token="x",
            expires_in=60,
            scope=frozenset({b"read"}),  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError, match="scopes"):
        OAuthTokenResponse(
            access_token="x",
            expires_in=60,
            scope=frozenset(f"scope-{index}" for index in range(1_025)),
        )
    with pytest.raises(ValidationError, match="discovery capability"):
        OIDCDiscoveryDocument(
            issuer="https://issuer.example",
            authorization_endpoint="https://issuer.example/authorize",
            token_endpoint="https://issuer.example/token",
            jwks_uri="https://issuer.example/jwks",
            response_types_supported=("code\n",),
        )
    with pytest.raises(ValidationError, match="unique"):
        OIDCDiscoveryDocument(
            issuer="https://issuer.example",
            authorization_endpoint="https://issuer.example/authorize",
            token_endpoint="https://issuer.example/token",
            jwks_uri="https://issuer.example/jwks",
            response_types_supported=("code", "code"),
        )
    with pytest.raises(ValidationError):
        OIDCDiscoveryDocument(
            issuer="https://issuer.example",
            authorization_endpoint="https://issuer.example/authorize",
            token_endpoint="https://issuer.example/token",
            jwks_uri="https://issuer.example/jwks",
            response_types_supported=tuple(f"type-{index}" for index in range(129)),
        )


@pytest.mark.parametrize(
    "factory",
    [
        lambda **extra: OAuthTokenResponse(access_token="x", expires_in=60, **extra),
        lambda **extra: OIDCDiscoveryDocument(
            issuer="https://issuer.example",
            authorization_endpoint="https://issuer.example/authorize",
            token_endpoint="https://issuer.example/token",
            jwks_uri="https://issuer.example/jwks",
            **extra,
        ),
    ],
)
def test_oauth_provider_extras_are_bounded(factory) -> None:
    """Forward-compatible provider fields cannot bypass Core metadata limits."""
    with pytest.raises(ValidationError, match="metadata"):
        factory(**{"bad\nfield": True})
    with pytest.raises(ValidationError, match="metadata"):
        factory(**{f"provider-{index}": index for index in range(129)})


def test_authorization_request_rejects_non_local_http_callbacks() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        OAuthAuthorizationRequest(
            client_id="client",
            redirect_uri="http://example.test/callback",
            state="1234567890123456",
        )
    request = OAuthAuthorizationRequest(
        client_id="client",
        redirect_uri="http://localhost:8000/callback",
        state="1234567890123456",
    )
    assert str(request.redirect_uri).startswith("http://localhost")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"client_id": "client\n1"},
        {"state": "12345678901234\n56"},
        {"code_challenge": "!" + "a" * 42},
        {"client_id": 1},
    ],
)
def test_authorization_request_rejects_unsafe_or_coerced_text(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "client_id": "client",
        "redirect_uri": "https://example.test/callback",
        "state": "1234567890123456",
    }
    values.update(kwargs)
    with pytest.raises(ValidationError):
        OAuthAuthorizationRequest(**values)  # type: ignore[arg-type]


def test_oidc_discovery_rejects_insecure_or_credential_bearing_urls() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        OIDCDiscoveryDocument(
            issuer="http://issuer.example",
            authorization_endpoint="https://issuer.example/authorize",
            token_endpoint="https://issuer.example/token",
            jwks_uri="https://issuer.example/jwks",
        )
    assert not is_https_url("https://user:secret@issuer.example")
    assert not is_https_url("https://issuer.example/callback#fragment")
