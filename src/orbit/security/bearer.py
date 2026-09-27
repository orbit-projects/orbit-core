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
"""Provider-neutral bearer authentication around an injected token verifier."""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from orbit.asgi.request import Headers, Request
from orbit.errors import ErrorCategory, OrbitProblem, SecurityError
from orbit.security.identity import Identity
from orbit.security.principal import Principal
from orbit.security.tokens import Token, TokenRevocationStore


@runtime_checkable
class TokenVerifier(Protocol):
    """Verify a raw credential and return validated token metadata."""

    def verify(self, credential: str) -> Token | Awaitable[Token]:
        """Validate signature, issuer, audience and claims in an adapter."""


class BearerAuthenticator:
    """Translate verified bearer credentials into Orbit principals."""

    def __init__(
        self,
        verifier: TokenVerifier | Callable[[str], Token | Awaitable[Token]],
        *,
        provider: str = "bearer",
        revocations: TokenRevocationStore | None = None,
    ) -> None:
        if not isinstance(provider, str) or re.fullmatch(r"[a-z][a-z0-9-]{0,62}", provider) is None:
            raise ValueError("provider must be a lowercase identifier.")
        if isinstance(verifier, TokenVerifier):
            if not callable(verifier.verify):
                raise TypeError("Token verifier must provide a callable verify method.")
        elif not callable(verifier):
            raise TypeError("Token verifier must be callable.")
        self._verifier = verifier
        self._provider = provider
        self._revocations = revocations or TokenRevocationStore()

    async def authenticate(self, request: Request) -> Principal | None:
        """Authenticate an optional RFC 6750 bearer header."""
        if not isinstance(request.headers, Headers):
            raise TypeError("Bearer authentication requires Core Headers.")
        authorization_values = request.headers.getall("authorization")
        if len(authorization_values) > 1:
            raise self._invalid()
        header = authorization_values[0] if authorization_values else None
        if header is None:
            return None
        scheme, separator, credential = header.partition(" ")
        if (
            not separator
            or scheme.lower() != "bearer"
            or not credential
            or len(credential) > 16_384
            or not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", credential)
        ):
            raise self._invalid()
        verifier = (
            self._verifier.verify if isinstance(self._verifier, TokenVerifier) else self._verifier
        )
        try:
            token = verifier(credential)
            if inspect.isawaitable(token):
                token = await token
        except Exception as exc:
            raise self._invalid() from exc
        if (
            not isinstance(token, Token)
            or token.is_expired()
            or self._revocations.is_revoked(token.token_id)
        ):
            raise self._invalid()
        return Principal(
            identity=Identity(
                subject=token.subject,
                provider=self._provider,
                claims={**token.claims, "token_id": token.token_id, "token_type": token.token_type},
            ),
            roles=token.scopes,
        )

    @staticmethod
    def _invalid() -> SecurityError:
        return SecurityError(
            OrbitProblem(
                code="security.invalid-token",
                message="Authentication credentials are invalid.",
                category=ErrorCategory.SECURITY,
            )
        )


__all__ = ["BearerAuthenticator", "TokenVerifier"]
