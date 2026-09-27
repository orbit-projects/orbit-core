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
"""Tests for provider-neutral token and JWKS contracts."""

from datetime import UTC, datetime, timedelta, tzinfo

import pytest

from orbit.security import JsonWebKey, JsonWebKeySet, PyJWTVerifier, Token, TokenValidationPolicy


@pytest.mark.parametrize(
    ("issued_at", "expires_at"),
    [
        (datetime.min.replace(tzinfo=UTC), datetime.min.replace(tzinfo=UTC) + timedelta(days=1)),
        (datetime.max.replace(tzinfo=UTC) - timedelta(days=1), datetime.max.replace(tzinfo=UTC)),
    ],
)
def test_token_policy_fails_closed_on_timestamp_arithmetic_overflow(
    issued_at: datetime, expires_at: datetime
) -> None:
    """Clock-skew arithmetic must not leak datetime overflow exceptions."""
    candidate = Token(
        token_id="edge-token",
        subject="user-1",
        issued_at=issued_at,
        expires_at=expires_at,
    )
    with pytest.raises(ValueError, match="validation range"):
        TokenValidationPolicy().validate(candidate, now=datetime(2026, 1, 1, tzinfo=UTC))


def token(**claims: object) -> Token:
    now = datetime.now(UTC)
    return Token(
        token_id="t1",
        subject="user",
        issued_at=now,
        expires_at=now + timedelta(minutes=5),
        scopes=frozenset({"read"}),
        claims=claims,
    )


def test_validation_policy_checks_issuer_audience_and_scopes() -> None:
    policy = TokenValidationPolicy(
        issuer="https://issuer", audience="api", required_scopes=frozenset({"read"})
    )
    assert policy.validate(token(iss="https://issuer", aud=["api"])).subject == "user"
    with pytest.raises(ValueError, match="issuer"):
        policy.validate(token(iss="https://other", aud="api"))
    with pytest.raises(ValueError, match="audience"):
        policy.validate(token(iss="https://issuer", aud="web"))
    with pytest.raises(ValueError, match="audience claim"):
        policy.validate(token(iss="https://issuer", aud=object()))


def test_jwks_snapshot_expires_and_selects_key() -> None:
    now = datetime.now(UTC)
    snapshot = JsonWebKeySet(
        keys=(JsonWebKey(kid="key-1", kty="RSA", alg="RS256"),),
        fetched_at=now,
        expires_at=now + timedelta(minutes=1),
    )
    assert snapshot.usable("key-1", now=now) is not None
    assert snapshot.usable("missing", now=now) is None
    assert snapshot.usable("key-1", now=now + timedelta(minutes=2)) is None


def test_jwks_snapshot_rejects_invalid_time_windows_and_duplicate_keys() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="timezone"):
        JsonWebKeySet(
            fetched_at=datetime(2026, 1, 1),
            expires_at=datetime(2026, 1, 2),
        )
    with pytest.raises(ValueError, match="after fetch"):
        JsonWebKeySet(fetched_at=now, expires_at=now)
    key = JsonWebKey(kid="duplicate", kty="RSA")
    with pytest.raises(ValueError, match="unique"):
        JsonWebKeySet(keys=(key, key), fetched_at=now, expires_at=now + timedelta(minutes=1))

    with pytest.raises(ValueError, match="key IDs"):
        JsonWebKeySet(fetched_at=now, expires_at=now + timedelta(minutes=1)).usable("")
    with pytest.raises(ValueError, match="key IDs"):
        JsonWebKeySet(fetched_at=now, expires_at=now + timedelta(minutes=1)).usable("key\n1")


def test_security_time_boundaries_reject_tzinfo_without_an_offset() -> None:
    class MissingOffset(tzinfo):
        def utcoffset(self, dt):
            return None

    ambiguous = datetime(2026, 1, 1, tzinfo=MissingOffset())
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="timezone"):
        JsonWebKeySet(fetched_at=ambiguous, expires_at=now + timedelta(minutes=1))
    snapshot = JsonWebKeySet(fetched_at=now, expires_at=now + timedelta(minutes=1))
    with pytest.raises(ValueError, match="timezone"):
        snapshot.usable("missing", now=ambiguous)
    with pytest.raises(ValueError, match="timezone"):
        TokenValidationPolicy().validate(token(), now=ambiguous)


def test_jwk_provider_extras_are_frozen_and_metadata_is_safe() -> None:
    key = JsonWebKey(kid="key-1", kty="RSA", provider_data={"uses": ["verify"]})
    with pytest.raises(TypeError, match="immutable"):
        key.model_extra["provider_data"]["uses"] = ["sign"]
    with pytest.raises(ValueError, match="control"):
        JsonWebKey(kid="bad\nkey", kty="RSA")
    with pytest.raises(ValueError, match="metadata"):
        JsonWebKey(kid="key-1", kty="RSA", **{"bad\nfield": True})
    with pytest.raises(ValueError, match="metadata"):
        JsonWebKey(
            kid="key-1",
            kty="RSA",
            **{f"provider-{index}": index for index in range(129)},
        )
    with pytest.raises(ValueError):
        JsonWebKey(kid=b"key-1", kty="RSA")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        JsonWebKey(kid="key-1", kty="RSA", alg=b"RS256")  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["alg", "use"])
def test_jwk_optional_metadata_cannot_be_empty(field: str) -> None:
    """Optional key-selection metadata is absent or meaningful, never an empty token."""
    with pytest.raises(ValueError):
        JsonWebKey(kid="key-1", kty="RSA", **{field: ""})


def test_jwks_key_count_is_bounded() -> None:
    now = datetime.now(UTC)
    keys = tuple(JsonWebKey(kid=f"key-{index}", kty="RSA") for index in range(1_001))
    with pytest.raises(ValueError):
        JsonWebKeySet(keys=keys, fetched_at=now, expires_at=now + timedelta(minutes=1))


def test_validation_policy_rejects_time_and_scope_claims() -> None:
    now = datetime.now(UTC)
    future = Token(
        token_id="future",
        subject="user",
        issued_at=now + timedelta(minutes=2),
        expires_at=now + timedelta(minutes=3),
    )
    with pytest.raises(ValueError, match="not valid"):
        TokenValidationPolicy(clock_skew=timedelta()).validate(future, now=now)
    with pytest.raises(ValueError, match="scopes"):
        TokenValidationPolicy(required_scopes=frozenset({"write"})).validate(token(), now=now)
    with pytest.raises(ValueError):
        Token(
            token_id="bytes-scope",
            subject="user",
            issued_at=now,
            expires_at=now + timedelta(minutes=1),
            scopes=frozenset({b"read"}),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        Token(
            token_id="bytes-claim",
            subject="user",
            issued_at=now,
            expires_at=now + timedelta(minutes=1),
            claims={b"iss": "issuer"},  # type: ignore[dict-item]
        )
    with pytest.raises(ValueError, match="timezone"):
        TokenValidationPolicy().validate(token(), now=datetime(2026, 1, 1))
    with pytest.raises(TypeError, match="Token validation"):
        TokenValidationPolicy().validate(object())  # type: ignore[arg-type]


def test_validation_policy_is_immutable_after_construction() -> None:
    policy = TokenValidationPolicy(issuer="https://issuer")
    with pytest.raises((TypeError, ValueError)):
        policy.issuer = "https://attacker"  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"issuer": ""},
        {"issuer": 1},
        {"issuer": "https://issuer\n"},
        {"audience": {"api"}},
        {"audience": frozenset({""})},
        {"audience": frozenset({"api\x7f"})},
        {"required_scopes": {"read"}},
        {"required_scopes": frozenset({""})},
        {"required_scopes": frozenset({"read\n"})},
        {"clock_skew": 1},
    ],
)
def test_validation_policy_rejects_invalid_configuration(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        TokenValidationPolicy(**kwargs)  # type: ignore[arg-type]


def test_validation_policy_bounds_trust_metadata_and_clock_skew() -> None:
    with pytest.raises(ValueError, match="issuer"):
        TokenValidationPolicy(issuer="i" * 256)
    with pytest.raises(ValueError, match="audience"):
        TokenValidationPolicy(audience=frozenset({"a" * 256}))
    with pytest.raises(ValueError, match="audience"):
        TokenValidationPolicy(audience=frozenset(str(index) for index in range(1_025)))
    with pytest.raises(ValueError, match="required_scopes"):
        TokenValidationPolicy(required_scopes=frozenset(str(index) for index in range(1_025)))
    with pytest.raises(ValueError, match="clock_skew"):
        TokenValidationPolicy(clock_skew=timedelta(days=1, seconds=1))


def test_pyjwt_verifier_requires_explicit_safe_configuration() -> None:
    with pytest.raises(ValueError, match="none"):
        PyJWTVerifier("secret", algorithms=("none",))
    with pytest.raises(ValueError, match="leeway"):
        PyJWTVerifier("secret", leeway=-1)
    with pytest.raises(ValueError, match="leeway"):
        PyJWTVerifier("secret", leeway=float("inf"))
    with pytest.raises(ValueError, match="leeway"):
        PyJWTVerifier("secret", leeway="1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="audience"):
        PyJWTVerifier("secret", audience=42)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="issuer"):
        PyJWTVerifier("secret", issuer=42)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="algorithms"):
        PyJWTVerifier("secret", algorithms=("HS256", 1))  # type: ignore[arg-type]
    verifier = PyJWTVerifier("secret")
    with pytest.raises(ValueError, match="invalid"):
        verifier.verify("x" * 16_385)
