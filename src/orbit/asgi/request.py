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
"""Bounded HTTP request data, duplicate-preserving headers and JSON validation."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from http.cookies import CookieError, SimpleCookie
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import parse_qs
from uuid import UUID

from pydantic import BaseModel

from orbit._limits import MAX_PATH_BYTES, MAX_PATH_PARAMETER_NAME_LENGTH, MAX_PATH_PARAMETERS
from orbit.types import RequestId, new_request_id

if TYPE_CHECKING:
    from orbit.container import Container
T = TypeVar("T", bound=BaseModel)
MAX_BODY_BYTES = 1024 * 1024 * 1024
MAX_HEADER_BYTES = 16 * 1024 * 1024
MAX_HEADER_COUNT = 1_000
MAX_QUERY_BYTES = 64 * 1024
_PATH_PARAMETER_NAME = re.compile(
    rf"[a-zA-Z_][a-zA-Z0-9_]{{0,{MAX_PATH_PARAMETER_NAME_LENGTH - 1}}}"
)
_COOKIE_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


class HTTPError(Exception):
    """An intentional HTTP failure with a validated, public, non-secret message."""

    def __init__(self, status: int, code: str, message: str) -> None:
        """Create a bounded error that can safely cross the HTTP response boundary."""
        if isinstance(status, bool) or not isinstance(status, int) or not 400 <= status <= 599:
            raise ValueError("HTTP error status must be an integer from 400 through 599.")
        if not isinstance(code, str) or re.fullmatch(r"[a-z][a-z0-9.-]{0,126}", code) is None:
            raise ValueError("HTTP error codes must be lowercase identifier-shaped strings.")
        if (
            not isinstance(message, str)
            or not 1 <= len(message) <= 1024
            or any(ord(character) < 32 or ord(character) == 127 for character in message)
        ):
            raise ValueError("HTTP error messages must be bounded printable text.")
        self.status = status
        self.code = code
        super().__init__(message)


class Headers(Mapping[str, str]):
    """Case-insensitive lookup plus ordered pairs for repeated HTTP headers.

    Header construction is a typed boundary: names and values must already be text and use valid
    HTTP field syntax. Hop-by-hop policy and aggregate size limits belong to the request/response
    boundary that owns the incoming or outgoing message.
    """

    def __init__(self, values: Mapping[str, str] | Sequence[tuple[str, str]] = ()) -> None:
        items = values.items() if isinstance(values, Mapping) else values
        pairs: list[tuple[str, str]] = []
        for name, value in items:
            if not isinstance(name, str) or not isinstance(value, str):
                raise TypeError("Header names and values must be strings.")
            normalized_name = name.lower()
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", normalized_name):
                raise ValueError("Invalid HTTP header name.")
            if any(
                (ord(character) < 32 and ord(character) != 9) or ord(character) == 127
                for character in value
            ):
                raise ValueError("Invalid HTTP header value.")
            try:
                value.encode("latin-1")
            except UnicodeEncodeError as exc:
                raise ValueError("HTTP header values must use Latin-1 text.") from exc
            pairs.append((normalized_name, value))
            if len(pairs) > MAX_HEADER_COUNT:
                raise ValueError("HTTP headers exceed the safety limit: maximum field count.")
            if sum(len(key) + len(item) + 2 for key, item in pairs) > MAX_HEADER_BYTES:
                raise ValueError("HTTP headers exceed the safety limit: maximum aggregate size.")
        self._pairs = tuple(pairs)

    @property
    def pairs(self) -> tuple[tuple[str, str], ...]:
        """Return every header in wire order, including duplicate Set-Cookie values."""
        return self._pairs

    def getall(self, name: str) -> tuple[str, ...]:
        """Return all values without invalid comma folding."""
        if not isinstance(name, str):
            raise TypeError("Header lookup names must be strings.")
        return tuple(value for key, value in self._pairs if key == name.lower())

    def __getitem__(self, name: str) -> str:
        values = self.getall(name)
        if not values:
            raise KeyError(name)
        return values[0]

    def __iter__(self) -> Iterator[str]:
        return iter(dict(self._pairs))

    def __len__(self) -> int:
        return len(dict(self._pairs))


@dataclass(frozen=True)
class Request:
    """Buffered request with scope-owned dependencies and an untrusted client boundary.

    ASGI supplies a decoded path; Orbit never percent-decodes it a second time. Headers
    retain duplicate fields. ``request_id`` is a server-generated ``RequestId`` and is not
    derived from client input. Direct path parameters are bounded canonical segments. A request
    container is valid only while its handler/stream runs.
    """

    method: str
    path: str
    headers: Mapping[str, str] = field(default_factory=Headers)
    body: bytes = b""
    query_string: bytes = b""
    path_parameters: Mapping[str, str] = field(default_factory=dict)
    container: Container | None = None
    request_id: RequestId = field(default_factory=new_request_id)
    root_path: str = ""
    client_host: str | None = None
    client_port: int | None = None
    scheme: str = "http"
    validated_body: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.method, str):
            raise TypeError("Request method must be a string.")
        if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,32}", self.method):
            raise ValueError("Request method is invalid.")
        object.__setattr__(self, "method", self.method.upper())
        if not isinstance(self.path, str):
            raise TypeError("Request path must be a string.")
        try:
            path_bytes = self.path.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Request path must contain valid Unicode text.") from exc
        if len(path_bytes) > MAX_PATH_BYTES:
            raise ValueError("Request path exceeds the maximum supported length.")
        if (
            not self.path.startswith("/")
            or "//" in self.path
            or "\\" in self.path
            or "?" in self.path
            or "#" in self.path
            or any(ord(character) < 32 or ord(character) == 127 for character in self.path)
            or any(segment in {".", ".."} for segment in self.path.split("/"))
        ):
            raise ValueError("Request path is not canonical.")
        if not isinstance(self.body, bytes) or not isinstance(self.query_string, bytes):
            raise TypeError("Request body and query string must be bytes.")
        if len(self.body) > MAX_BODY_BYTES:
            raise ValueError("Request body exceeds the maximum supported length.")
        if len(self.query_string) > MAX_QUERY_BYTES:
            raise ValueError("Request query string exceeds the maximum supported length.")
        if not isinstance(self.root_path, str):
            raise TypeError("Request root path must be a string.")
        try:
            root_path_bytes = self.root_path.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("Request root path must contain valid Unicode text.") from exc
        if len(root_path_bytes) > MAX_PATH_BYTES:
            raise ValueError("Request root path exceeds the maximum supported length.")
        if self.root_path and (
            not self.root_path.startswith("/")
            or "//" in self.root_path
            or "\\" in self.root_path
            or "?" in self.root_path
            or "#" in self.root_path
            or self.root_path.endswith("/")
            and self.root_path != "/"
            or any(segment in {".", ".."} for segment in self.root_path.split("/"))
        ):
            raise ValueError("Request root path is not canonical.")
        if self.client_host is not None:
            if not isinstance(self.client_host, str):
                raise TypeError("Request client host must be a string or None.")
            if (
                not self.client_host
                or len(self.client_host) > 255
                or any(
                    character.isspace() or ord(character) < 32 or ord(character) == 127
                    for character in self.client_host
                )
            ):
                raise ValueError("Request client host is invalid.")
        if self.client_port is not None and (
            isinstance(self.client_port, bool)
            or not isinstance(self.client_port, int)
            or not 0 <= self.client_port <= 65535
        ):
            raise ValueError("Request client port is invalid.")
        if not isinstance(self.scheme, str):
            raise TypeError("Request scheme must be a string.")
        scheme = self.scheme.lower()
        if scheme not in {"http", "https"}:
            raise ValueError("Request scheme is invalid.")
        object.__setattr__(self, "scheme", scheme)
        if not isinstance(self.request_id, UUID):
            raise TypeError("Request ID must be a UUID-backed RequestId.")
        object.__setattr__(
            self,
            "headers",
            self.headers if isinstance(self.headers, Headers) else Headers(self.headers),
        )
        if not isinstance(self.path_parameters, Mapping):
            raise TypeError("Request path parameters must be a mapping.")
        # Validate while copying: callers can provide custom mappings whose reported length is
        # inaccurate, so materializing the whole mapping before enforcing the route bound is unsafe.
        parameters: dict[str, str] = {}
        for parameter_count, (key, value) in enumerate(self.path_parameters.items(), start=1):
            if parameter_count > MAX_PATH_PARAMETERS:
                raise ValueError("Request path parameters exceed the safety limit.")
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("Request path parameter names and values must be strings.")
            if _PATH_PARAMETER_NAME.fullmatch(key) is None:
                raise ValueError("Request path parameter names must be safe identifiers.")
            try:
                value_size = len(value.encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise ValueError(
                    "Request path parameter values must contain valid Unicode."
                ) from exc
            if (
                not value
                or value_size > MAX_PATH_BYTES
                or value in {".", ".."}
                or any(character in value for character in "/\\?#")
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError("Request path parameter values must be canonical path segments.")
            parameters[key] = value
        object.__setattr__(self, "path_parameters", MappingProxyType(parameters))

    @property
    def query_parameters(self) -> dict[str, list[str]]:
        """Parse UTF-8 query values with a field-count bound and repeated values preserved."""
        if len(self.query_string) > MAX_QUERY_BYTES:
            raise HTTPError(414, "request.query-too-large", "Query string is too large.")
        try:
            return parse_qs(
                self.query_string.decode("ascii"),
                keep_blank_values=True,
                max_num_fields=1000,
                encoding="utf-8",
                errors="strict",
            )
        except (UnicodeError, ValueError) as exc:
            raise HTTPError(400, "request.invalid-query", "Invalid query string.") from exc

    @property
    def cookies(self) -> Mapping[str, str]:
        """Parse the Cookie header into a detached mapping with bounded input size."""
        headers = self.headers if isinstance(self.headers, Headers) else Headers(self.headers)
        cookie_values = headers.getall("cookie")
        if len(cookie_values) > 1:
            raise HTTPError(400, "request.cookie", "Duplicate Cookie headers are not allowed.")
        value = cookie_values[0] if cookie_values else ""
        if len(value) > 16 * 1024:
            raise HTTPError(400, "request.cookies-too-large", "Cookie header is too large.")
        seen_names: set[str] = set()
        for pair in value.split(";"):
            name, separator, _ = pair.strip().partition("=")
            if separator and _COOKIE_NAME.fullmatch(name):
                if name in seen_names:
                    raise HTTPError(
                        400,
                        "request.cookie",
                        "Duplicate cookie names are not allowed.",
                    )
                seen_names.add(name)
        parsed = SimpleCookie()
        try:
            parsed.load(value)
        except CookieError as exc:
            raise HTTPError(400, "request.invalid-cookies", "Invalid Cookie header.") from exc
        return MappingProxyType({name: morsel.value for name, morsel in parsed.items()})

    def json(self) -> Any:
        """Parse a JSON body, rejecting incorrect content types, non-finite values and bad UTF-8."""
        headers = self.headers if isinstance(self.headers, Headers) else Headers(self.headers)
        content_types = headers.getall("content-type")
        if len(content_types) > 1:
            raise HTTPError(
                400,
                "request.content-type",
                "Duplicate Content-Type headers are not allowed.",
            )
        media = (content_types[0] if content_types else "").partition(";")[0].strip().lower()
        if media != "application/json" and not media.endswith("+json"):
            raise HTTPError(415, "request.media-type", "A JSON content type is required.")

        def reject(value: str) -> None:
            """Raise a bounded public HTTP error without exposing parser internals."""
            raise ValueError(f"Non-finite JSON constant: {value}")

        try:
            return json.loads(self.body.decode("utf-8"), parse_constant=reject)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise HTTPError(400, "request.invalid-json", "Invalid JSON document.") from exc

    def validate(self, model: type[T]) -> T:
        """Validate JSON using the caller's Pydantic schema."""
        return model.model_validate(self.json())


__all__ = [
    "HTTPError",
    "Headers",
    "MAX_BODY_BYTES",
    "MAX_HEADER_BYTES",
    "MAX_HEADER_COUNT",
    "MAX_PATH_BYTES",
    "MAX_QUERY_BYTES",
    "Request",
]
