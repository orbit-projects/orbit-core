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
"""Verify token validation, bearer authentication, and bounded revocation state."""

from datetime import UTC, datetime, timedelta, tzinfo
from types import SimpleNamespace

import pytest

from orbit.asgi import Request
from orbit.errors import SecurityError
from orbit.security import BearerAuthenticator, Identity, Token, TokenRevocationStore
from orbit.security.context import bind_principal


def token(*, token_id: str = "token-1", expires: datetime | None = None) -> Token:
    issued = datetime.now(UTC)
    return Token(
        token_id=token_id,
        subject="user-1",
        issued_at=issued,
        expires_at=expires or issued + timedelta(minutes=5),
    )


def test_security_context_requires_a_principal_instance() -> None:
    """The runtime binder preserves the typed Principal context contract."""
    with pytest.raises(TypeError, match="Principal"):
        bind_principal({})  # type: ignore[arg-type]


def test_token_expiry_and_revocation_are_bounded():
    current = datetime.now(UTC)
    with pytest.raises(ValueError):
        token(expires=current - timedelta(seconds=1))

    valid = token()
    store = TokenRevocationStore(max_entries=1)
    assert store.revoke(valid)
    assert not store.revoke(valid)
    assert store.is_revoked(valid.token_id)
    assert store.size == 1
    with pytest.raises(ValueError, match="positive"):
        TokenRevocationStore(max_entries=True)
    with pytest.raises(ValueError, match="positive"):
        TokenRevocationStore(max_entries="1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="1,000,000"):
        TokenRevocationStore(max_entries=1_000_001)


def test_token_rejects_naive_timestamps():
    with pytest.raises(ValueError, match="timezone"):
        Token(
            token_id="naive",
            subject="user-1",
            issued_at=datetime(2026, 1, 1),
            expires_at=datetime(2026, 1, 2),
        )


def test_security_identity_fields_reject_implicit_string_coercion() -> None:
    """Verified identity metadata must be text at the trust boundary, not coerced into text."""
    with pytest.raises(ValueError):
        Token(
            token_id=1,  # type: ignore[arg-type]
            subject="user-1",
            issued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
    with pytest.raises(ValueError):
        Token(
            token_id="token-1",
            subject=1,  # type: ignore[arg-type]
            issued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
    with pytest.raises(ValueError):
        Identity(subject=1, provider="test")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Identity(subject="user-1", provider=1)  # type: ignore[arg-type]


def test_security_provider_and_token_type_reject_control_characters() -> None:
    """Security metadata cannot carry control characters through regex boundaries."""
    with pytest.raises(ValueError, match="provider"):
        Identity(subject="user-1", provider="idp\n")
    with pytest.raises(ValueError):
        Token(
            token_id="token-1",
            subject="user-1",
            token_type="access\n",
            issued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )


def test_token_boundaries_reject_tzinfo_without_a_usable_offset() -> None:
    class MissingOffset(tzinfo):
        def utcoffset(self, dt):
            return None

    ambiguous = datetime(2026, 1, 1, tzinfo=MissingOffset())
    with pytest.raises(ValueError, match="timezone"):
        Token(
            token_id="ambiguous",
            subject="user-1",
            issued_at=ambiguous,
            expires_at=datetime(2026, 1, 2, tzinfo=MissingOffset()),
        )
    valid = token()
    with pytest.raises(ValueError, match="timezone"):
        valid.is_expired(ambiguous)
    with pytest.raises(ValueError, match="timezone"):
        TokenRevocationStore().revoke(valid, now=ambiguous)
    with pytest.raises(ValueError, match="timezone"):
        TokenRevocationStore().is_revoked(valid.token_id, now=ambiguous)


@pytest.mark.parametrize("field", ["token_id", "subject"])
def test_token_identifiers_reject_control_characters(field: str) -> None:
    values = {"token_id": "token\n1", "subject": "user\x7f"}
    kwargs = {
        "token_id": "token-1",
        "subject": "user-1",
        "issued_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC) + timedelta(minutes=1),
    }
    kwargs[field] = values[field]
    with pytest.raises(ValueError, match="control"):
        Token(**kwargs)  # type: ignore[arg-type]


def test_token_comparisons_reject_naive_clock_values():
    valid = token()
    with pytest.raises(ValueError, match="timezone"):
        valid.is_expired(datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="timezone"):
        TokenRevocationStore().revoke(valid, now=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="timezone"):
        TokenRevocationStore().is_revoked(valid.token_id, now=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="Token IDs"):
        TokenRevocationStore().is_revoked("")
    with pytest.raises(ValueError, match="Token IDs"):
        TokenRevocationStore().is_revoked("x" * 256)
    with pytest.raises(ValueError, match="Token IDs"):
        TokenRevocationStore().is_revoked("token\n1")


def test_revocation_store_expires_entries_and_rejects_capacity():
    first = token()
    second = token(token_id="token-2")
    store = TokenRevocationStore(max_entries=1)
    assert store.revoke(first)
    with pytest.raises(RuntimeError):
        store.revoke(second)


def test_revocation_store_purges_by_real_expiry_order_across_dst_fold() -> None:
    """Heap cleanup orders expiry instants, not repeated local wall-clock values."""

    class FallBackTimezone(tzinfo):
        def utcoffset(self, dt):
            return timedelta(hours=-4 if dt is None or dt.fold == 0 else -5)

        def dst(self, dt):
            return timedelta(0)

    zone = FallBackTimezone()
    issued = datetime(2026, 11, 1, 0, 0, tzinfo=zone)
    earlier_expiry = Token(
        token_id="earlier",
        subject="user-1",
        issued_at=issued,
        expires_at=datetime(2026, 11, 1, 1, 10, tzinfo=zone, fold=0),
    )
    later_expiry = Token(
        token_id="later",
        subject="user-1",
        issued_at=issued,
        expires_at=datetime(2026, 11, 1, 1, 15, tzinfo=zone, fold=1),
    )
    before_expiry = datetime(2026, 11, 1, 1, 0, tzinfo=zone, fold=0)
    after_first_expiry = datetime(2026, 11, 1, 1, 12, tzinfo=zone, fold=1)
    store = TokenRevocationStore(max_entries=2)

    assert store.revoke(later_expiry, now=before_expiry)
    assert store.revoke(earlier_expiry, now=before_expiry)
    assert not store.is_revoked("earlier", now=after_first_expiry)
    assert store.is_revoked("later", now=after_first_expiry)
    replacement = Token(
        token_id="replacement",
        subject="user-1",
        issued_at=issued,
        expires_at=datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1),
    )
    assert store.revoke(replacement, now=after_first_expiry)


async def test_bearer_authenticator_verifies_and_maps_principal():
    issued = datetime.now(UTC)
    verified = Token(
        token_id="t1",
        subject="user-1",
        issued_at=issued,
        expires_at=issued + timedelta(minutes=1),
        scopes=frozenset({"reader"}),
        claims={"tenant": "acme"},
    )
    authenticator = BearerAuthenticator(lambda credential: verified, provider="jwt")
    principal = await authenticator.authenticate(
        Request("GET", "/", headers={"authorization": "Bearer credential"})
    )
    assert principal is not None
    assert principal.identity.subject == "user-1"
    assert principal.identity.claims["tenant"] == "acme"
    assert principal.roles == frozenset({"reader"})


def test_verified_claims_are_detached_and_immutable() -> None:
    claims = {"tenant": "acme", "groups": ["reader"], "metadata": {"region": "eu"}}
    verified = token()
    verified = Token(
        token_id=verified.token_id,
        subject=verified.subject,
        issued_at=verified.issued_at,
        expires_at=verified.expires_at,
        claims=claims,
    )

    claims["tenant"] = "other"
    claims["groups"].append("admin")

    assert verified.claims["tenant"] == "acme"
    assert verified.claims["groups"] == ("reader",)
    with pytest.raises(TypeError, match="immutable"):
        verified.claims["tenant"] = "other"  # type: ignore[index]
    with pytest.raises(TypeError, match="immutable"):
        verified.claims["metadata"]["region"] = "us"  # type: ignore[index]


@pytest.mark.parametrize("model", [Token, Identity])
@pytest.mark.parametrize("claims", [{"bad\nkey": "value"}, {"": "value"}, {"k" * 256: "value"}])
def test_verified_claims_reject_unsafe_keys(model, claims) -> None:
    """Token and identity models apply the same safe claim-key contract."""
    if model is Token:
        kwargs = {
            "token_id": "token-1",
            "subject": "user-1",
            "issued_at": datetime.now(UTC),
            "expires_at": datetime.now(UTC) + timedelta(minutes=1),
            "claims": claims,
        }
    else:
        kwargs = {"subject": "user-1", "provider": "test", "claims": claims}
    with pytest.raises(ValueError, match="claims keys"):
        model(**kwargs)


@pytest.mark.parametrize("model", [Token, Identity])
def test_verified_claims_reject_excessive_cardinality(model) -> None:
    """Verified claim mappings have a fixed cardinality limit."""
    claims = {f"claim-{index}": index for index in range(1_025)}
    if model is Token:
        kwargs = {
            "token_id": "token-1",
            "subject": "user-1",
            "issued_at": datetime.now(UTC),
            "expires_at": datetime.now(UTC) + timedelta(minutes=1),
            "claims": claims,
        }
    else:
        kwargs = {"subject": "user-1", "provider": "test", "claims": claims}
    with pytest.raises(ValueError, match="1,024"):
        model(**kwargs)


async def test_bearer_authenticator_rejects_malformed_and_expired_tokens():
    with pytest.raises(SecurityError):
        await BearerAuthenticator(lambda credential: None).authenticate(
            Request("GET", "/", headers={"authorization": "Basic abc"})
        )
    issued = datetime.now(UTC) - timedelta(minutes=2)
    expired = Token(
        token_id="expired",
        subject="user-1",
        issued_at=issued,
        expires_at=issued + timedelta(minutes=1),
    )
    with pytest.raises(SecurityError):
        await BearerAuthenticator(lambda credential: expired).authenticate(
            Request("GET", "/", headers={"authorization": "Bearer abc"})
        )
    for credential in ("token value", "token\tvalue", "!invalid"):
        with pytest.raises(SecurityError):
            await BearerAuthenticator(lambda raw: None).authenticate(
                Request("GET", "/", headers={"authorization": f"Bearer {credential}"})
            )


async def test_bearer_authenticator_handles_missing_headers_and_verifier_failures():
    """Missing credentials remain anonymous while verifier failures are sanitized."""
    authenticator = BearerAuthenticator(lambda _: token())
    assert await authenticator.authenticate(Request("GET", "/")) is None

    def failing_verifier(_: str) -> Token:
        raise RuntimeError("provider detail must not cross the security boundary")

    with pytest.raises(SecurityError, match="invalid"):
        await BearerAuthenticator(failing_verifier).authenticate(
            Request("GET", "/", headers={"authorization": "Bearer credential"})
        )

    with pytest.raises(TypeError, match="Core Headers"):
        await authenticator.authenticate(SimpleNamespace(headers={}))  # type: ignore[arg-type]


def test_bearer_authenticator_rejects_protocol_verifier_without_callable_method() -> None:
    """Runtime protocol checks must still require a callable verifier method."""

    class InvalidVerifier:
        verify = object()

    with pytest.raises(TypeError, match="callable verify"):
        BearerAuthenticator(InvalidVerifier())


async def test_bearer_authenticator_rejects_duplicate_authorization_headers():
    verified = token()
    with pytest.raises(SecurityError):
        await BearerAuthenticator(lambda _: verified).authenticate(
            Request(
                "GET",
                "/",
                headers=[
                    ("authorization", "Bearer first"),
                    ("authorization", "Bearer second"),
                ],
            )
        )


@pytest.mark.parametrize("provider", ["", "JWT", "bad_provider", "a" * 64])
def test_bearer_provider_identifier_is_strict(provider):
    with pytest.raises(ValueError):
        BearerAuthenticator(lambda _: token(), provider=provider)


async def test_bearer_authenticator_supports_async_verifier_and_revocation():
    verified = token()

    async def verify(_: str):
        return verified

    revocations = TokenRevocationStore()
    authenticator = BearerAuthenticator(verify, revocations=revocations)
    request = Request("GET", "/", headers={"authorization": "Bearer credential"})
    assert await authenticator.authenticate(request) is not None
    revocations.revoke(verified)
    with pytest.raises(SecurityError):
        await authenticator.authenticate(request)


def test_bearer_provider_must_be_a_string() -> None:
    with pytest.raises(ValueError):
        BearerAuthenticator(lambda _: token(), provider=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="verifier"):
        BearerAuthenticator(object())  # type: ignore[arg-type]
