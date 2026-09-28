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
"""Tests for the optional PyJWT integration without requiring PyJWT in Core's test env."""

import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from orbit.security import PyJWTVerifier


def test_pyjwt_verifier_maps_verified_claims(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC).timestamp()
    calls = {}

    def decode(credential, key, **kwargs):
        calls.update(credential=credential, key=key, kwargs=kwargs)
        return {
            "sub": "user-1",
            "jti": "token-1",
            "iat": now,
            "exp": now + 60,
            "scope": "read write",
            "iss": "https://issuer.example",
        }

    monkeypatch.setitem(sys.modules, "jwt", SimpleNamespace(decode=decode))
    token = PyJWTVerifier(
        "secret", issuer="https://issuer.example", audience="api", algorithms=("HS256",)
    ).verify("compact")
    assert token.subject == "user-1"
    assert token.scopes == frozenset({"read", "write"})
    assert calls["kwargs"]["algorithms"] == ["HS256"]
    assert calls["kwargs"]["options"] == {"require": ["sub", "jti", "iat", "exp"]}


def test_pyjwt_verifier_hides_backend_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def decode(*args, **kwargs):
        raise RuntimeError("signature details")

    monkeypatch.setitem(sys.modules, "jwt", SimpleNamespace(decode=decode))
    with pytest.raises(ValueError, match="invalid") as error:
        PyJWTVerifier("secret").verify("compact")
    assert "signature details" not in str(error.value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"issuer": "x" * 256},
        {"issuer": "issuer\n"},
        {"audience": "x" * 256},
        {"audience": ("api", "x" * 256)},
        {"audience": tuple(f"api-{index}" for index in range(1_025))},
        {"algorithms": ("HS256", "none")},
        {"algorithms": ("HS\n256",)},
        {"algorithms": tuple(f"ALG{index}" for index in range(17))},
    ],
)
def test_pyjwt_verifier_bounds_policy_metadata(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        PyJWTVerifier("secret", **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("credential", ["", 123])
def test_pyjwt_verifier_rejects_empty_or_non_string_credentials(credential: object) -> None:
    with pytest.raises(ValueError, match="invalid"):
        PyJWTVerifier("secret").verify(credential)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": 1, "jti": "id", "iat": 1, "exp": 2},
        {"sub": "u", "jti": "id", "iat": 1, "exp": 2, "scope": [""]},
        {"sub": "u", "jti": "id", "iat": 1, "exp": 2, "typ": 1},
    ],
)
def test_pyjwt_verifier_rejects_malformed_claim_types(
    monkeypatch: pytest.MonkeyPatch, claims: dict[str, object]
) -> None:
    monkeypatch.setitem(sys.modules, "jwt", SimpleNamespace(decode=lambda *args, **kwargs: claims))
    with pytest.raises(ValueError, match="invalid"):
        PyJWTVerifier("secret").verify("compact")


def test_pyjwt_verifier_bounds_scope_claims_before_token_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims = {
        "sub": "u",
        "jti": "id",
        "iat": 1,
        "exp": 2,
        "scope": [f"scope-{index}" for index in range(1_025)],
    }
    monkeypatch.setitem(sys.modules, "jwt", SimpleNamespace(decode=lambda *args, **kwargs: claims))
    with pytest.raises(ValueError, match="invalid"):
        PyJWTVerifier("secret").verify("compact")


@pytest.mark.parametrize("claim", ["iat", "exp"])
def test_pyjwt_verifier_rejects_non_numeric_date_claims(
    monkeypatch: pytest.MonkeyPatch, claim: str
) -> None:
    claims = {"sub": "u", "jti": "id", "iat": 1, "exp": 2}
    claims[claim] = True
    monkeypatch.setitem(sys.modules, "jwt", SimpleNamespace(decode=lambda *args, **kwargs: claims))
    with pytest.raises(ValueError, match="invalid"):
        PyJWTVerifier("secret").verify("compact")
