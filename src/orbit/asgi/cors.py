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
"""Strict CORS middleware for the custom ASGI request pipeline."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from orbit.asgi.middleware import NextHandler
from orbit.asgi.request import Headers, Request
from orbit.asgi.response import Response


class CORSMiddleware:
    """Apply an explicit origin policy and validate CORS preflight requests."""

    def __init__(
        self,
        *,
        allow_origins: Iterable[str] = (),
        allow_methods: Iterable[str] = ("GET", "HEAD", "OPTIONS"),
        allow_headers: Iterable[str] = (),
        expose_headers: Iterable[str] = (),
        allow_credentials: bool = False,
        max_age: int = 600,
    ) -> None:
        if not isinstance(allow_credentials, bool):
            raise TypeError("CORS allow_credentials must be a boolean.")
        origins = tuple(
            dict.fromkeys(
                origin.strip()
                for origin in self._strings(allow_origins, "origins")
                if origin.strip()
            )
        )
        if "*" in origins and allow_credentials:
            raise ValueError("Wildcard origins cannot be combined with credentials.")
        for origin in origins:
            if origin != "*":
                self._validate_origin(origin)
        methods = tuple(
            dict.fromkeys(
                method.strip().upper() for method in self._strings(allow_methods, "methods")
            )
        )
        if not methods or any(not self._is_token(method) for method in methods):
            raise ValueError("CORS methods must be nonempty HTTP method names.")
        headers = self._header_names(allow_headers, "allowed headers")
        exposed = self._header_names(expose_headers, "exposed headers")
        if (
            isinstance(max_age, bool)
            or not isinstance(max_age, int)
            or max_age < 0
            or max_age > 86_400
        ):
            raise ValueError("CORS max_age must be between 0 and 86400 seconds.")
        self._allow_origins = origins
        self._allow_methods = methods
        self._allow_headers = headers
        self._expose_headers = exposed
        self._allow_credentials = allow_credentials
        self._max_age = max_age

    @property
    def allow_origins(self) -> tuple[str, ...]:
        """Return the fixed allowed-origin policy."""
        return self._allow_origins

    @property
    def allow_methods(self) -> tuple[str, ...]:
        """Return the fixed preflight method policy."""
        return self._allow_methods

    @property
    def allow_headers(self) -> tuple[str, ...]:
        """Return the fixed preflight request-header policy."""
        return self._allow_headers

    @property
    def expose_headers(self) -> tuple[str, ...]:
        """Return the fixed response-header exposure policy."""
        return self._expose_headers

    @property
    def allow_credentials(self) -> bool:
        """Return whether credentialed cross-origin requests are allowed."""
        return self._allow_credentials

    @property
    def max_age(self) -> int:
        """Return the fixed preflight cache duration."""
        return self._max_age

    @staticmethod
    def _strings(values: Iterable[str], label: str) -> tuple[str, ...]:
        """Materialize a string iterable and reject scalar or non-text policy values."""
        if isinstance(values, (str, bytes)):
            raise TypeError(f"CORS {label} must be an iterable of strings, not a scalar.")
        try:
            items = tuple(values)
        except TypeError as exc:
            raise TypeError(f"CORS {label} must be an iterable of strings.") from exc
        if any(not isinstance(item, str) for item in items):
            raise TypeError(f"CORS {label} must contain only strings.")
        return items

    @classmethod
    def _header_names(cls, values: Iterable[str], label: str) -> tuple[str, ...]:
        """Normalize and validate header-token policy entries."""
        names = tuple(dict.fromkeys(item.strip().lower() for item in cls._strings(values, label)))
        if any(not cls._is_token(name) for name in names):
            raise ValueError(f"CORS {label} must contain HTTP header names.")
        return names

    @staticmethod
    def _is_token(value: str) -> bool:
        """Return whether a value is a valid HTTP token for CORS policy metadata."""
        return bool(re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", value))

    @staticmethod
    def _validate_origin(origin: str) -> None:
        """Reject ambiguous origins so policy entries cannot match unexpectedly."""
        try:
            parsed = urlsplit(origin)
            hostname = parsed.hostname
            _ = parsed.port  # force validation of malformed ports
        except ValueError as exc:
            raise ValueError("CORS origins must be valid HTTP or HTTPS origins.") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or "%" in hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.netloc.endswith(":")
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "?" in origin
            or "#" in origin
            or any(
                character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
                for character in origin
            )
        ):
            raise ValueError("CORS origins must be valid HTTP or HTTPS origins.")

    async def __call__(self, request: Request, next_handler: NextHandler) -> Response:
        if not isinstance(request.headers, Headers):
            raise TypeError("CORS middleware requires Core Headers for duplicate detection.")
        headers = request.headers
        origins = headers.getall("origin")
        # Origin is a single-valued browser policy input. Treat duplicates as ambiguous instead
        # of allowing a trusted first value to hide a conflicting value from a proxy or client.
        origin = origins[0] if len(origins) == 1 else None
        if not origin:
            return await next_handler(request)
        allowed_origin = self._allowed_origin(origin)
        if request.method == "OPTIONS" and headers.get("access-control-request-method"):
            if allowed_origin is None:
                return Response.json({"code": "security.cors-origin"}, status=403)
            requested_methods = headers.getall("access-control-request-method")
            requested_header_values = headers.getall("access-control-request-headers")
            if len(requested_methods) != 1 or len(requested_header_values) > 1:
                return Response.json({"code": "security.cors-preflight"}, status=403)
            requested_method = requested_methods[0].upper()
            requested_headers = tuple(
                item.strip().lower()
                for item in (requested_header_values[0] if requested_header_values else "").split(
                    ","
                )
                if item.strip()
            )
            if requested_method not in self.allow_methods or not set(requested_headers).issubset(
                self.allow_headers
            ):
                return Response.json({"code": "security.cors-preflight"}, status=403)
            response = Response(status=204)
            return self._headers(response, allowed_origin, preflight=True)
        response = await next_handler(request)
        if allowed_origin is None:
            return response
        return self._headers(response, allowed_origin)

    def _allowed_origin(self, origin: str) -> str | None:
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in origin):
            return None
        if origin in self.allow_origins:
            return origin
        return "*" if "*" in self.allow_origins and not self.allow_credentials else None

    def _headers(self, response: Response, origin: str, *, preflight: bool = False) -> Response:
        if not isinstance(response.headers, Headers):
            raise TypeError("Responses must expose validated headers.")
        managed = {
            "access-control-allow-origin",
            "access-control-allow-credentials",
            "access-control-expose-headers",
            "access-control-allow-methods",
            "access-control-allow-headers",
            "access-control-max-age",
        }
        headers: list[tuple[str, str]] = []
        vary: list[str] = []
        for name, value in response.headers.pairs:
            if name in managed:
                continue
            if name == "vary":
                vary.append(value)
                continue
            headers.append((name, value))
        headers.append(("access-control-allow-origin", origin))
        vary_tokens: dict[str, str] = {}
        for token in (token.strip() for value in vary for token in value.split(",")):
            if token:
                vary_tokens.setdefault(token.lower(), token)
        if origin != "*" and "*" not in vary_tokens:
            vary_tokens.setdefault("origin", "Origin")
        if vary_tokens:
            headers.append(("vary", ", ".join(vary_tokens.values())))
        if self.allow_credentials:
            headers.append(("access-control-allow-credentials", "true"))
        if self.expose_headers and not preflight:
            headers.append(("access-control-expose-headers", ", ".join(self.expose_headers)))
        if preflight:
            headers.extend(
                [
                    ("access-control-allow-methods", ", ".join(self.allow_methods)),
                    ("access-control-allow-headers", ", ".join(self.allow_headers)),
                    ("access-control-max-age", str(self.max_age)),
                ]
            )
        return Response(response.status, response.body, headers, response.stream)


__all__ = ["CORSMiddleware"]
