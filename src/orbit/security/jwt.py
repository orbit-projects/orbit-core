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
"""Optional PyJWT-backed bearer token verification with strict claim policy."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from orbit._limits import is_finite_number
from orbit.security.tokens import Token

_MAX_JWT_ALGORITHMS = 16
_MAX_JWT_AUDIENCES = 1_024


class PyJWTVerifier:
    """Verify JWTs through PyJWT while keeping Orbit's token contract provider-neutral.

    PyJWT is an optional security extra. Algorithms and required claims are explicit so
    the verifier cannot silently accept unsigned tokens or tokens without an identity,
    issuance time, expiry, or revocation identifier.
    """

    def __init__(
        self,
        key: Any,
        *,
        algorithms: tuple[str, ...] = ("HS256",),
        issuer: str | None = None,
        audience: str | tuple[str, ...] | None = None,
        leeway: float = 0,
    ) -> None:
        if (
            not isinstance(algorithms, tuple)
            or not algorithms
            or len(algorithms) > _MAX_JWT_ALGORITHMS
            or any(
                not isinstance(algorithm, str)
                or not 1 <= len(algorithm) <= 32
                or algorithm.lower() == "none"
                or any(ord(character) < 33 or ord(character) == 127 for character in algorithm)
                for algorithm in algorithms
            )
        ):
            raise ValueError("JWT algorithms must explicitly exclude 'none'.")
        if (
            isinstance(leeway, bool)
            or not isinstance(leeway, (int, float))
            or not is_finite_number(leeway)
            or leeway < 0
        ):
            raise ValueError("JWT leeway cannot be negative.")
        if issuer is not None and (
            not isinstance(issuer, str)
            or not 1 <= len(issuer) <= 255
            or any(ord(character) < 32 or ord(character) == 127 for character in issuer)
        ):
            raise ValueError("JWT issuer must be a nonempty string when provided.")
        if audience is not None and (
            not isinstance(audience, (str, tuple))
            or (
                isinstance(audience, str)
                and (
                    not 1 <= len(audience) <= 255
                    or any(ord(character) < 32 or ord(character) == 127 for character in audience)
                )
            )
            or (
                isinstance(audience, tuple)
                and (
                    not audience
                    or len(audience) > _MAX_JWT_AUDIENCES
                    or any(not isinstance(item, str) or not item for item in audience)
                )
            )
        ):
            raise ValueError("JWT audience must be a nonempty string or tuple of strings.")
        if isinstance(audience, tuple) and any(
            len(item) > 255
            or any(ord(character) < 32 or ord(character) == 127 for character in item)
            for item in audience
        ):
            raise ValueError("JWT audience values must be bounded printable strings.")
        self._key = key
        self._algorithms = algorithms
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway

    def verify(self, credential: str) -> Token:
        """Decode and validate one compact JWT, returning payload-free verified metadata."""
        if not isinstance(credential, str) or not credential or len(credential) > 16_384:
            raise ValueError("JWT credential is invalid.")
        try:
            import jwt  # type: ignore[import-not-found]

            claims = jwt.decode(
                credential,
                self._key,
                algorithms=list(self._algorithms),
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway,
                options={"require": ["sub", "jti", "iat", "exp"]},
            )
            issued_at = datetime.fromtimestamp(_numeric_date(claims["iat"], "iat"), UTC)
            expires_at = datetime.fromtimestamp(_numeric_date(claims["exp"], "exp"), UTC)
            subject = claims["sub"]
            token_id = claims["jti"]
            if not isinstance(subject, str) or not isinstance(token_id, str):
                raise ValueError("JWT subject and ID must be strings.")
            raw_scopes = claims.get("scope", claims.get("scp", ()))
            scopes = (
                frozenset(raw_scopes.split())
                if isinstance(raw_scopes, str)
                else frozenset(raw_scopes)
            )
            if any(not isinstance(scope, str) or not scope for scope in scopes):
                raise ValueError("JWT scopes must be nonempty strings.")
            token_type = claims.get("typ", "access")
            if not isinstance(token_type, str):
                raise ValueError("JWT type must be a string.")
            return Token(
                token_id=token_id,
                subject=subject,
                token_type=token_type.lower(),
                issued_at=issued_at,
                expires_at=expires_at,
                scopes=scopes,
                claims=dict(claims),
            )
        except Exception as exc:
            raise ValueError("JWT credential is invalid.") from exc


def _numeric_date(value: object, claim: str) -> float:
    """Require finite numeric JWT dates without accepting booleans or text coercion."""
    if not is_finite_number(value):
        raise ValueError(f"JWT {claim} claim must be a finite number.")
    return float(value)


__all__ = ["PyJWTVerifier"]
