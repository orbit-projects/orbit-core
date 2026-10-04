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

from orbit.security import JsonWebKey, JsonWebKeySet, Token, TokenValidationPolicy


@pytest.mark.parametrize(
    ("issued_at", "expires_at", "now"),
    [
        (
            datetime.min.replace(tzinfo=UTC),
            datetime.min.replace(tzinfo=UTC) + timedelta(days=1),
            datetime.min.replace(tzinfo=UTC) + timedelta(hours=1),
        ),
        (
            datetime.max.replace(tzinfo=UTC) - timedelta(days=1),
            datetime.max.replace(tzinfo=UTC),
            datetime.max.replace(tzinfo=UTC) - timedelta(hours=1),
        ),
    ],
)
def test_token_policy_compares_boundary_timestamps_without_arithmetic_overflow(
    issued_at: datetime, expires_at: datetime, now: datetime
) -> None:
    """Exact instant comparisons remain valid at both datetime representation limits."""
    candidate = Token(
        token_id="edge-token",
        subject="user-1",
        issued_at=issued_at,
        expires_at=expires_at,
    )
    policy = TokenValidationPolicy(clock_skew=timedelta(0))
    assert policy.validate(candidate, now=now) is candidate


class _FallBackTimezone(tzinfo):
    """Minimal DST-like zone whose repeated hour has two distinct UTC offsets."""

    def utcoffset(self, dt):
        return timedelta(hours=-4 if dt is None or dt.fold == 0 else -5)

    def dst(self, dt):
        return timedelta(0)


def test_security_expiry_compares_real_instants_across_repeated_local_hour() -> None:
    """Token policy and JWKS freshness must honor ``fold`` for a repeated local hour."""
    zone = _FallBackTimezone()
    issued = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)  # 05:30 UTC
    current = datetime(2026, 11, 1, 1, 10, tzinfo=zone, fold=1)  # 06:10 UTC
    expires = datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1)  # 06:15 UTC
    verified = Token(
        token_id="fold-token",
        subject="user",
        issued_at=issued,
        expires_at=expires,
    )

    assert not verified.is_expired(current)
    assert (
        TokenValidationPolicy(clock_skew=timedelta(0)).validate(verified, now=current) is verified
    )
    snapshot = JsonWebKeySet(
        keys=(JsonWebKey(kid="fold-key", kty="RSA"),),
        fetched_at=issued,
        expires_at=expires,
    )
    assert snapshot.usable("fold-key", now=current) is not None
    after_expiry = datetime(2026, 11, 1, 1, 20, tzinfo=zone, fold=1)
    assert snapshot.usable("fold-key", now=after_expiry) is None


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
    with pytest.raises(ValueError, match="cardinality"):
        policy.validate(token(iss="https://issuer", aud=[f"aud-{index}" for index in range(1_025)]))
    with pytest.raises(ValueError, match="unsafe text"):
        policy.validate(token(iss="https://issuer", aud=["api\n"]))


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
