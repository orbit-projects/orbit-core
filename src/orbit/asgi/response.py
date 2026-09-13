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

import json
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from orbit.asgi.request import Headers


@dataclass(frozen=True)
class Response:
    """A complete response; a stream stays inside the request's resource scope.

    HTTP framing and TLS belong to the host. Content-Length is computed for buffered
    bodies. Streaming content length is omitted. Header injection and hop-by-hop headers
    are rejected before response.start.
    """

    status: int = 200
    body: bytes = b""
    headers: Mapping[str, str] | Sequence[tuple[str, str]] = field(default_factory=dict)
    stream: AsyncIterator[bytes] | None = None

    def __post_init__(self) -> None:
        if not 200 <= self.status <= 599:
            raise ValueError("Response status must be final (200..599).")
        if not isinstance(self.body, bytes) or (self.stream is not None and self.body):
            raise ValueError("Use a bytes body or a stream, not both.")
        headers = self.headers if isinstance(self.headers, Headers) else Headers(self.headers)
        for name, value in headers.pairs:
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9a-z-]+", name):
                raise ValueError("Invalid response header name.")
            if "\r" in value or "\n" in value or "\x00" in value:
                raise ValueError("Invalid response header value.")
            value.encode("latin-1")
            if name in {"connection", "transfer-encoding", "keep-alive", "upgrade"}:
                raise ValueError("Hop-by-hop headers belong to the ASGI server.")
        object.__setattr__(self, "headers", headers)

    def wire_headers(self) -> list[tuple[bytes, bytes]]:
        """Return safe lower-case headers with runtime-controlled content length."""
        headers = self.headers
        assert isinstance(headers, Headers)
        pairs = [(name, value) for name, value in headers.pairs if name != "content-length"]
        if self.stream is None and self.status not in {204, 304}:
            pairs.append(("content-length", str(len(self.body))))
        return [(name.encode("ascii"), value.encode("latin-1")) for name, value in pairs]

    @classmethod
    def text(cls, content: str, *, status: int = 200) -> Response:
        """Encode UTF-8 plain text."""
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
