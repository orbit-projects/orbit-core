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
"""Opt-in HTTP Basic authentication with salted password hashes and bounded verification."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import re
import secrets
import unicodedata
from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictInt, StrictStr, field_validator

from orbit.asgi.request import Headers, Request
from orbit.errors import ErrorCategory, OrbitProblem, SecurityError
from orbit.security.identity import Identity
from orbit.security.principal import Principal
from orbit.security.roles import validate_role_collection

_PBKDF2_ITERATIONS: Final = 600_000
_MAX_ITERATIONS: Final = 1_000_000
_MAX_CREDENTIALS: Final = 10_000
_MAX_HEADER_CREDENTIAL_BYTES: Final = 8_192
_MAX_USER_PASS_BYTES: Final = 4_096
_MAX_USERNAME_BYTES: Final = 256
_MAX_PASSWORD_BYTES: Final = 1_024
_HASH_BYTES: Final = 32
_SALT_BYTES: Final = 16


class BasicCredential(BaseModel):
    """One user record containing a salted PBKDF2 hash, never a plaintext password."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, validate_default=True)

    username: StrictStr = Field(min_length=1, max_length=256)
    salt: StrictStr = Field(pattern=r"^[A-Za-z0-9_-]{22}$")
    password_hash: SecretStr
    iterations: StrictInt = Field(
        default=_PBKDF2_ITERATIONS, ge=_PBKDF2_ITERATIONS, le=_MAX_ITERATIONS
    )
    roles: frozenset[StrictStr] = Field(default_factory=frozenset)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        """Apply the RFC 7617 UTF-8 normalization and safe username bounds."""
        normalized = _normalize_component(value, name="username", max_bytes=_MAX_USERNAME_BYTES)
        if ":" in normalized:
            raise ValueError("Basic usernames cannot contain a colon.")
        return normalized

    @field_validator("password_hash")
    @classmethod
    def validate_hash(cls, value: SecretStr) -> SecretStr:
        """Accept only a fixed-length lowercase hexadecimal PBKDF2 digest."""
        if re.fullmatch(r"[0-9a-f]{64}", value.get_secret_value()) is None:
            raise ValueError("Basic password hashes must be 32-byte lowercase hexadecimal values.")
        return value

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, value: frozenset[str]) -> frozenset[str]:
        """Use Orbit's shared role validation before associating roles with an identity."""
        return validate_role_collection(value)

    @classmethod
    def create(
        cls,
        username: str,
        password: SecretStr | str,
        *,
        roles: Sequence[str] = (),
        iterations: int = _PBKDF2_ITERATIONS,
    ) -> BasicCredential:
        """Create a salted hash for application configuration without retaining the password.

        Call this only during trusted configuration/bootstrap work. Provide the password from a
        secret store; the returned model stores a random salt and derived digest only.
        """
        if (
            isinstance(iterations, bool)
            or not isinstance(iterations, int)
            or not _PBKDF2_ITERATIONS <= iterations <= _MAX_ITERATIONS
        ):
            raise ValueError("PBKDF2 iterations must be between 600,000 and 1,000,000.")
        secret = password.get_secret_value() if isinstance(password, SecretStr) else password
        if not isinstance(secret, str):
            raise TypeError("Basic passwords must be text or SecretStr.")
        normalized_username = _normalize_component(
            username, name="username", max_bytes=_MAX_USERNAME_BYTES
        )
        if ":" in normalized_username:
            raise ValueError("Basic usernames cannot contain a colon.")
        normalized_password = _normalize_component(
            secret, name="password", max_bytes=_MAX_PASSWORD_BYTES
        )
        salt = secrets.token_bytes(_SALT_BYTES)
        digest = hashlib.pbkdf2_hmac(
            "sha256", normalized_password.encode("utf-8"), salt, iterations, dklen=_HASH_BYTES
        )
        salt_text = base64.urlsafe_b64encode(salt).rstrip(b"=").decode("ascii")
        return cls(
            username=normalized_username,
            salt=salt_text,
            password_hash=SecretStr(digest.hex()),
            iterations=iterations,
            roles=frozenset(roles),
        )

    def _salt_bytes(self) -> bytes:
        """Decode the validated fixed-length salt for verification."""
        return base64.urlsafe_b64decode(self.salt + "==")

    def _digest_bytes(self) -> bytes:
        """Decode the secret-wrapped fixed-length PBKDF2 digest."""
        return bytes.fromhex(self.password_hash.get_secret_value())


class BasicAuthenticator:
    """Authenticate configured users using HTTP Basic over HTTPS by default.

    Verification is offloaded from the event loop and limited by a per-authenticator semaphore.
    This static user store is intended for local/admin baselines, not account lifecycle, federation,
    session management, or a replacement for a dedicated identity provider.
    """

    def __init__(
        self,
        credentials: Sequence[BasicCredential],
        *,
        realm: str = "Orbit",
        require_tls: bool = True,
        max_concurrent_verifications: int = 4,
    ) -> None:
        """Configure explicitly supplied hashes and a bounded PBKDF2 work policy."""
        if isinstance(credentials, (str, bytes)) or not isinstance(credentials, Sequence):
            raise TypeError("Basic credentials must be a sequence of BasicCredential models.")
        if not 1 <= len(credentials) <= _MAX_CREDENTIALS:
            raise ValueError("Basic authentication requires between 1 and 10,000 credentials.")
        detached = tuple(credentials)
        if any(not isinstance(item, BasicCredential) for item in detached):
            raise TypeError("Every Basic credential must be a BasicCredential model.")
        if len({item.username for item in detached}) != len(detached):
            raise ValueError("Basic usernames must be unique after Unicode normalization.")
        if len({item.iterations for item in detached}) != 1:
            raise ValueError("All Basic credentials must use the same PBKDF2 work factor.")
        if (
            not isinstance(realm, str)
            or not 1 <= len(realm) <= 128
            or any(ord(character) < 32 or ord(character) > 126 for character in realm)
        ):
            raise ValueError("Basic realm must be 1 to 128 printable ASCII characters.")
        if not isinstance(require_tls, bool):
            raise TypeError("require_tls must be a boolean.")
        if (
            isinstance(max_concurrent_verifications, bool)
            or not isinstance(max_concurrent_verifications, int)
            or not 1 <= max_concurrent_verifications <= 128
        ):
            raise ValueError("max_concurrent_verifications must be between 1 and 128.")
        quoted_realm = realm.replace("\\", "\\\\").replace('"', '\\"')
        self.challenge = f'Basic realm="{quoted_realm}", charset="UTF-8"'
        self._credentials = {item.username: item for item in detached}
        self._iterations = detached[0].iterations
        self._require_tls = require_tls
        self._max_concurrent_verifications = max_concurrent_verifications
        self._dummy_salt = secrets.token_bytes(_SALT_BYTES)
        self._dummy_digest = secrets.token_bytes(_HASH_BYTES)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._semaphore: asyncio.Semaphore | None = None

    async def authenticate(self, request: Request) -> Principal | None:
        """Authenticate one optional Basic header, returning ``None`` when it is absent."""
        if not isinstance(request, Request) or not isinstance(request.headers, Headers):
            raise TypeError("Basic authentication requires a Core Request and Headers.")
        values = request.headers.getall("authorization")
        if not values:
            return None
        if len(values) != 1:
            raise self._invalid()
        if self._require_tls and request.scheme != "https":
            raise self._invalid()
        username, password = _decode_authorization(values[0])
        credential = self._credentials.get(username)
        salt = credential._salt_bytes() if credential is not None else self._dummy_salt
        expected = credential._digest_bytes() if credential is not None else self._dummy_digest
        supplied = await self._verify(password, salt, expected)
        if credential is None or not supplied:
            raise self._invalid()
        return Principal(
            identity=Identity(subject=credential.username, provider="basic"),
            roles=credential.roles,
        )

    async def _verify(self, password: bytes, salt: bytes, expected: bytes) -> bool:
        """Perform comparable-cost hashing off-loop while bounding concurrent CPU work."""
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
            self._semaphore = asyncio.Semaphore(self._max_concurrent_verifications)
        elif self._loop is not loop:
            raise RuntimeError("BasicAuthenticator is bound to another event loop.")
        semaphore = self._semaphore
        if semaphore is None:
            raise RuntimeError("BasicAuthenticator verification semaphore is unavailable.")
        async with semaphore:
            work = asyncio.create_task(
                asyncio.to_thread(_verify_password, password, salt, expected, self._iterations)
            )
            try:
                return await asyncio.shield(work)
            except asyncio.CancelledError:
                # asyncio cannot stop a PBKDF2 call already running in a worker. Keep the bounded
                # permit until that work finishes. Repeated cancellation must not release it early
                # and let detached hash jobs exceed this authenticator's configured capacity.
                while not work.done():
                    try:
                        await asyncio.shield(work)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                raise

    @staticmethod
    def _invalid() -> SecurityError:
        """Return the same generic failure for malformed, unknown, and incorrect credentials."""
        return SecurityError(
            OrbitProblem(
                code="security.invalid-basic-credentials",
                message="Authentication credentials are invalid.",
                category=ErrorCategory.SECURITY,
            )
        )


def _decode_authorization(header: str) -> tuple[str, bytes]:
    """Strictly decode a bounded Basic header into normalized UTF-8 username and password."""
    if not isinstance(header, str) or len(header) > _MAX_HEADER_CREDENTIAL_BYTES:
        raise BasicAuthenticator._invalid()
    scheme, separator, encoded = header.partition(" ")
    if not separator or scheme.lower() != "basic" or not encoded:
        raise BasicAuthenticator._invalid()
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise BasicAuthenticator._invalid() from None
    if not raw or len(raw) > _MAX_USER_PASS_BYTES or b":" not in raw:
        raise BasicAuthenticator._invalid()
    username_bytes, password = raw.split(b":", 1)
    try:
        username = username_bytes.decode("utf-8")
        password_text = password.decode("utf-8")
    except UnicodeDecodeError:
        raise BasicAuthenticator._invalid() from None
    try:
        normalized_username = _normalize_component(
            username, name="username", max_bytes=_MAX_USERNAME_BYTES
        )
        normalized_password = _normalize_component(
            password_text, name="password", max_bytes=_MAX_PASSWORD_BYTES
        )
    except (TypeError, ValueError):
        raise BasicAuthenticator._invalid() from None
    if ":" in normalized_username:
        raise BasicAuthenticator._invalid()
    return normalized_username, normalized_password.encode("utf-8")


def _normalize_component(value: str, *, name: str, max_bytes: int) -> str:
    """Normalize an RFC Basic text component and reject empty, oversized, or control text."""
    if not isinstance(value, str):
        raise TypeError(f"Basic {name} must be text.")
    normalized = unicodedata.normalize("NFC", value)
    encoded = normalized.encode("utf-8")
    if (
        not encoded
        or len(encoded) > max_bytes
        or any(unicodedata.category(character) == "Cc" for character in normalized)
    ):
        raise ValueError(f"Basic {name} is empty, oversized, or contains control characters.")
    return normalized


def _verify_password(password: bytes, salt: bytes, expected: bytes, iterations: int) -> bool:
    """Derive a SHA-256 PBKDF2 digest and compare it in constant time."""
    actual = hashlib.pbkdf2_hmac("sha256", password, salt, iterations, dklen=_HASH_BYTES)
    return hmac.compare_digest(actual, expected)


__all__ = ["BasicAuthenticator", "BasicCredential"]
