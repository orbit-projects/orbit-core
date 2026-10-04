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
"""Core Basic authentication stores only bounded, salted password hashes."""

import asyncio
import base64
import threading

import pytest
from pydantic import SecretStr, ValidationError

from orbit.asgi.request import Headers, Request
from orbit.errors import SecurityError
from orbit.security import BasicAuthenticator, BasicCredential


def _request(value: str | None, *, scheme: str = "https") -> Request:
    headers = Headers() if value is None else Headers({"authorization": value})
    return Request(method="GET", path="/private", headers=headers, scheme=scheme)


def _basic(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def test_credential_creation_stores_salted_digest_not_password() -> None:
    credential = BasicCredential.create(
        "operator", SecretStr("correct horse"), roles=("orbit.admin.read",)
    )

    assert credential.username == "operator"
    assert credential.password_hash.get_secret_value() != "correct horse"
    assert credential.password_hash.get_secret_value() not in credential.model_dump_json()
    assert credential.salt
    assert credential.roles == frozenset({"orbit.admin.read"})


@pytest.mark.parametrize("username", ["", "operator:admin", "bad\nname"])
def test_credential_creation_rejects_unsafe_usernames(username: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        BasicCredential.create(username, "secret")


async def test_authenticator_accepts_valid_credentials_and_returns_principal() -> None:
    credential = BasicCredential.create("operator", "correct horse", roles=("ops",))
    authenticator = BasicAuthenticator([credential])

    principal = await authenticator.authenticate(_request(_basic("operator", "correct horse")))

    assert principal is not None
    assert principal.identity.subject == "operator"
    assert principal.identity.provider == "basic"
    assert principal.roles == frozenset({"ops"})


async def test_authenticator_ignores_absent_header_and_rejects_bad_credentials() -> None:
    authenticator = BasicAuthenticator([BasicCredential.create("operator", "correct horse")])

    assert await authenticator.authenticate(_request(None)) is None
    for authorization in (
        _basic("operator", "wrong"),
        _basic("missing", "wrong"),
        "Basic !!!",
        "Bearer token",
    ):
        with pytest.raises(SecurityError, match="Authentication credentials are invalid"):
            await authenticator.authenticate(_request(authorization))


async def test_authenticator_requires_tls_before_hashing() -> None:
    authenticator = BasicAuthenticator([BasicCredential.create("operator", "correct horse")])

    with pytest.raises(SecurityError):
        await authenticator.authenticate(
            _request(_basic("operator", "correct horse"), scheme="http")
        )


async def test_repeated_cancellation_keeps_verification_capacity_until_hash_finishes(
    monkeypatch,
) -> None:
    """A second cancel cannot detach PBKDF2 work and free its bounded semaphore permit."""
    import orbit.security.basic as basic

    credential = BasicCredential.create("operator", "correct horse")
    authenticator = BasicAuthenticator([credential], max_concurrent_verifications=1)
    worker_started = threading.Event()
    release_worker = threading.Event()
    second_worker_started = threading.Event()
    worker_calls = 0
    original_verify = basic._verify_password

    def delayed_verify(password: bytes, salt: bytes, expected: bytes, iterations: int) -> bool:
        nonlocal worker_calls
        worker_calls += 1
        if worker_calls == 1:
            worker_started.set()
            if not release_worker.wait(timeout=5):
                raise TimeoutError("Test did not release the password-hash worker.")
        else:
            second_worker_started.set()
        return original_verify(password, salt, expected, iterations)

    monkeypatch.setattr(basic, "_verify_password", delayed_verify)
    request = _request(_basic("operator", "correct horse"))
    first = asyncio.create_task(authenticator.authenticate(request))
    try:
        assert await asyncio.to_thread(worker_started.wait, 2)
        first.cancel()
        await asyncio.sleep(0)
        assert not first.done()

        first.cancel()
        await asyncio.sleep(0)
        assert not first.done()

        second = asyncio.create_task(authenticator.authenticate(request))
        await asyncio.sleep(0.02)
        assert worker_calls == 1
        assert not second.done()
        assert not second_worker_started.is_set()
    finally:
        release_worker.set()

    with pytest.raises(asyncio.CancelledError):
        await first
    principal = await second
    assert principal is not None
    assert worker_calls == 2


def test_duplicate_normalized_usernames_are_rejected() -> None:
    first = BasicCredential.create("é", "one")
    second = BasicCredential.create("e\u0301", "two")

    with pytest.raises(ValueError, match="unique"):
        BasicAuthenticator([first, second])
