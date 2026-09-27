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
"""Validated HTTP responses, repeated headers and async streaming bodies."""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from typing import Any, ClassVar

from pydantic import BaseModel

from orbit.asgi.request import Headers


@dataclass(frozen=True)
class Response:
    """A complete response; a stream stays inside the request's resource scope.

    HTTP framing and TLS belong to the host. Content-Length is computed for buffered
    bodies. Streaming content length is omitted. Header injection and hop-by-hop headers
    are rejected before response.start. If a stream exposes ``aclose``, it must be an
    async callable so cleanup can be bounded without blocking the event loop.
    """

    status: int = 200
    body: bytes = b""
    headers: Mapping[str, str] | Sequence[tuple[str, str]] = field(default_factory=dict)
    stream: AsyncIterator[bytes] | None = None

    MAX_HEADER_BYTES: ClassVar[int] = 64 * 1024
    MAX_HEADER_COUNT: ClassVar[int] = 1000
    MAX_BODY_BYTES: ClassVar[int] = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        if (
            isinstance(self.status, bool)
            or not isinstance(self.status, int)
            or not 200 <= self.status <= 599
        ):
            raise ValueError("Response status must be final (200..599).")
        if not isinstance(self.body, bytes) or (self.stream is not None and self.body):
            raise ValueError("Use a bytes body or a stream, not both.")
        if self.status in {204, 304} and (self.body or self.stream is not None):
            raise ValueError("204 and 304 responses cannot contain a body.")
        if self.stream is not None:
            try:
                iterator = aiter(self.stream)
            except (AttributeError, TypeError) as exc:
                raise TypeError("Response streams must be asynchronous iterators.") from exc
            if not callable(getattr(iterator, "__anext__", None)):
                raise TypeError("Response streams must implement asynchronous iteration.")
            close = getattr(self.stream, "aclose", None)
            if close is not None and (
                not callable(close)
                or not (inspect.iscoroutinefunction(close) or inspect.isasyncgen(self.stream))
            ):
                raise TypeError("Response stream cleanup must be an async callable.")
        headers = self.headers if isinstance(self.headers, Headers) else Headers(self.headers)
        if len(headers.getall("content-type")) > 1:
            raise ValueError("Responses cannot contain duplicate Content-Type headers.")
        if (
            len(headers.pairs) > self.MAX_HEADER_COUNT
            or sum(len(name) + len(value) + 2 for name, value in headers.pairs)
            > self.MAX_HEADER_BYTES
        ):
            raise ValueError("Response headers exceed the configured safety limit.")
        for name, value in headers.pairs:
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9a-z-]+", name):
                raise ValueError("Invalid response header name.")
            if any(
                (ord(character) < 32 and ord(character) != 9) or ord(character) == 127
                for character in value
            ):
                raise ValueError("Invalid response header value.")
            value.encode("latin-1")
            if name in {"connection", "transfer-encoding", "keep-alive", "upgrade"}:
                raise ValueError("Hop-by-hop headers belong to the ASGI server.")
        object.__setattr__(self, "headers", headers)

    def wire_headers(self) -> list[tuple[bytes, bytes]]:
        """Return safe lower-case headers with runtime-controlled content length."""
        headers = self.headers
        if not isinstance(headers, Headers):
            raise TypeError("Responses must expose validated headers.")
        pairs = [(name, value) for name, value in headers.pairs if name != "content-length"]
        if self.stream is None and self.status not in {204, 304}:
            pairs.append(("content-length", str(len(self.body))))
        return [(name.encode("ascii"), value.encode("latin-1")) for name, value in pairs]

    def with_cookie(
        self,
        name: str,
        value: str,
        *,
        max_age: int | None = None,
        expires: str | None = None,
        path: str | None = "/",
        domain: str | None = None,
        secure: bool = False,
        httponly: bool = False,
        samesite: str | None = "lax",
    ) -> Response:
        """Return a response with one validated, repeated-safe Set-Cookie header."""
        if not isinstance(name, str) or not re.fullmatch(
            r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,256}", name
        ):
            raise ValueError("Cookie names must be nonempty HTTP tokens.")
        if not isinstance(value, str):
            raise TypeError("Cookie values must be strings.")
        for attribute_name, attribute in (
            ("expires", expires),
            ("path", path),
            ("domain", domain),
        ):
            if attribute is not None and (
                not isinstance(attribute, str)
                or any(ord(character) < 32 or ord(character) == 127 for character in attribute)
            ):
                raise ValueError(f"Cookie {attribute_name} must be safe text.")
        if not isinstance(secure, bool) or not isinstance(httponly, bool):
            raise TypeError("Cookie secure and httponly flags must be booleans.")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Cookie values cannot contain control characters.")
        if max_age is not None and (
            not isinstance(max_age, int) or isinstance(max_age, bool) or max_age < 0
        ):
            raise ValueError("max_age must be a nonnegative integer.")
        if samesite is not None and not isinstance(samesite, str):
            raise TypeError("samesite must be a string or None.")
        normalized_samesite = samesite.lower() if samesite is not None else None
        if normalized_samesite is not None and normalized_samesite not in {"lax", "strict", "none"}:
            raise ValueError("samesite must be lax, strict or none.")
        if normalized_samesite == "none" and not secure:
            raise ValueError("SameSite=None cookies must be Secure.")
        cookie = SimpleCookie()
        try:
            cookie[name] = value
            morsel = cookie[name]
            if max_age is not None:
                morsel["max-age"] = str(max_age)
            if expires is not None:
                morsel["expires"] = expires
            if path is not None:
                morsel["path"] = path
            if domain is not None:
                morsel["domain"] = domain
            if secure:
                morsel["secure"] = True
            if httponly:
                morsel["httponly"] = True
            if samesite is not None:
                morsel["samesite"] = normalized_samesite
            rendered = morsel.OutputString()
            rendered.encode("latin-1")
        except (CookieError, TypeError, UnicodeError, ValueError) as exc:
            raise ValueError("Invalid cookie attributes.") from exc
        if not isinstance(self.headers, Headers):
            raise TypeError("Responses must expose validated headers.")
        headers = [*self.headers.pairs, ("set-cookie", rendered)]
        return Response(self.status, self.body, headers, self.stream)

    @classmethod
    def text(cls, content: str, *, status: int = 200) -> Response:
        """Encode bounded caller-supplied text as UTF-8, rejecting non-text inputs explicitly."""
        if not isinstance(content, str):
            raise TypeError("Response text content must be a string.")
        return cls(status, content.encode(), {"content-type": "text/plain; charset=utf-8"})

    @classmethod
    def json(cls, content: Any, *, status: int = 200) -> Response:
        """Serialize JSON strictly; Pydantic models use secret-aware JSON serialization."""
        if isinstance(content, BaseModel):
            content = content.model_dump(mode="json")
        body = json.dumps(content, separators=(",", ":"), allow_nan=False).encode()
        return cls(status, body, {"content-type": "application/json"})

    @classmethod
    def streaming(
        cls,
        chunks: AsyncIterator[bytes],
        *,
        content_type: str = "application/octet-stream",
        status: int = 200,
    ) -> Response:
        """Send asynchronous chunks while honoring host backpressure."""
        return cls(status=status, headers={"content-type": content_type}, stream=chunks)


__all__ = ["Response"]
