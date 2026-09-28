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
"""Opt-in response compression middleware with strict negotiation semantics."""

from __future__ import annotations

import gzip
import re
from collections.abc import Sequence

from orbit.asgi.middleware import NextHandler
from orbit.asgi.request import Headers, Request
from orbit.asgi.response import Response

_QVALUE_PATTERN = re.compile(r"(?:0(?:\.[0-9]{1,3})?|1(?:\.0{1,3})?)\Z")
_MAX_ACCEPT_ENCODING_BYTES = 64 * 1024
_MAX_ACCEPT_ENCODING_TOKENS = 1_024


class GZipMiddleware:
    """Compress eligible buffered text responses when the client accepts gzip."""

    def __init__(self, *, minimum_size: int = 500, compresslevel: int = 6) -> None:
        if (
            isinstance(minimum_size, bool)
            or not isinstance(minimum_size, int)
            or minimum_size < 0
            or isinstance(compresslevel, bool)
            or not isinstance(compresslevel, int)
            or not 1 <= compresslevel <= 9
        ):
            raise ValueError("Compression limits are invalid.")
        self._minimum_size = minimum_size
        self._compresslevel = compresslevel

    @property
    def minimum_size(self) -> int:
        """Return the fixed minimum response size eligible for compression."""
        return self._minimum_size

    @property
    def compresslevel(self) -> int:
        """Return the fixed gzip compression level."""
        return self._compresslevel

    async def __call__(self, request: Request, next_handler: NextHandler) -> Response:
        response = await next_handler(request)
        if not self._eligible(request, response):
            return response
        if not isinstance(response.headers, Headers):
            raise TypeError("Responses must expose validated headers.")
        headers: list[tuple[str, str]] = []
        vary: list[str] = []
        for name, value in response.headers.pairs:
            if name == "content-length":
                continue
            if name == "vary":
                vary.extend(token.strip() for token in value.split(",") if token.strip())
                continue
            headers.append((name, value))
        compressed = gzip.compress(response.body, compresslevel=self.compresslevel, mtime=0)
        vary.append("Accept-Encoding")
        headers.extend(
            (
                ("content-encoding", "gzip"),
                ("vary", ", ".join(dict.fromkeys(vary))),
            )
        )
        return Response(response.status, compressed, headers)

    def _eligible(self, request: Request, response: Response) -> bool:
        if response.stream is not None or len(response.body) < self.minimum_size:
            return False
        if response.status in {204, 304}:
            return False
        if not isinstance(response.headers, Headers):
            raise TypeError("Responses must expose validated headers.")
        content_type = response.headers.get("content-type", "").partition(";")[0].lower()
        if not (
            content_type.startswith("text/")
            or content_type
            in {"application/json", "application/javascript", "application/xml", "image/svg+xml"}
        ):
            return False
        if response.headers.get("content-encoding") is not None:
            return False
        if not isinstance(request.headers, Headers):
            raise TypeError("Compression middleware requires Core Headers for negotiation.")
        return self._accepts_gzip(request.headers.getall("accept-encoding"))

    @staticmethod
    def _accepts_gzip(values: Sequence[str]) -> bool:
        """Return whether bounded, duplicate-free negotiation explicitly permits gzip."""
        if len(values) > _MAX_ACCEPT_ENCODING_TOKENS:
            return False
        total_bytes = 0
        explicit: float | None = None
        wildcard: float | None = None
        seen: set[str] = set()
        token_count = 0
        for value in values:
            total_bytes += len(value)
            if total_bytes > _MAX_ACCEPT_ENCODING_BYTES:
                return False
            for token in value.split(","):
                token_count += 1
                if token_count > _MAX_ACCEPT_ENCODING_TOKENS:
                    return False
                encoding, _, parameters = token.strip().lower().partition(";")
                if encoding not in {"gzip", "*"}:
                    continue
                if encoding in seen:
                    return False
                seen.add(encoding)
                quality = 1.0
                quality_seen = False
                for parameter in parameters.split(";"):
                    key, separator, raw = parameter.strip().partition("=")
                    if key.lower() == "q" and separator:
                        if quality_seen:
                            return False
                        quality_seen = True
                        if _QVALUE_PATTERN.fullmatch(raw) is None:
                            return False
                        quality = float(raw)
                if encoding == "gzip":
                    explicit = quality
                else:
                    wildcard = quality
        return (explicit if explicit is not None else wildcard or 0) > 0


__all__ = ["GZipMiddleware"]
