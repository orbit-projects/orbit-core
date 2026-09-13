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
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import parse_qs

from pydantic import BaseModel

if TYPE_CHECKING:
    from orbit.container import Container
T = TypeVar("T", bound=BaseModel)


class HTTPError(Exception):
    """An intentional HTTP failure with a public, non-secret message."""

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        super().__init__(message)


class Headers(Mapping[str, str]):
    """Case-insensitive lookup plus raw ordered pairs for repeated HTTP headers."""

    def __init__(self, values: Mapping[str, str] | Sequence[tuple[str, str]] = ()) -> None:
        items = values.items() if isinstance(values, Mapping) else values
        self._pairs = tuple((name.lower(), value) for name, value in items)

    @property
    def pairs(self) -> tuple[tuple[str, str], ...]:
        """Return every header in wire order, including duplicate Set-Cookie values."""
        return self._pairs

    def getall(self, name: str) -> tuple[str, ...]:
        """Return all values without invalid comma folding."""
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
    retain duplicate fields. A request container is valid only while its handler/stream runs.
    """

    method: str
    path: str
    headers: Mapping[str, str] = field(default_factory=Headers)
    body: bytes = b""
    query_string: bytes = b""
    path_parameters: Mapping[str, str] = field(default_factory=dict)
    container: Container | None = None
    request_id: str = ""
    root_path: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "headers",
            self.headers if isinstance(self.headers, Headers) else Headers(self.headers),
        )
        object.__setattr__(self, "path_parameters", MappingProxyType(dict(self.path_parameters)))

    @property
    def query_parameters(self) -> dict[str, list[str]]:
        """Parse UTF-8 query values with a field-count bound and repeated values preserved."""
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

    def json(self) -> Any:
        """Parse a JSON body, rejecting incorrect content types, non-finite values and bad UTF-8."""
        media = self.headers.get("content-type", "").partition(";")[0].strip().lower()
        if media != "application/json" and not media.endswith("+json"):
            raise HTTPError(415, "request.media-type", "A JSON content type is required.")

        def reject(value: str) -> None:
            raise ValueError(f"Non-finite JSON constant: {value}")

        try:
            return json.loads(self.body.decode("utf-8"), parse_constant=reject)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise HTTPError(400, "request.invalid-json", "Invalid JSON document.") from exc

    def validate(self, model: type[T]) -> T:
        """Validate JSON using the caller's Pydantic schema."""
        return model.model_validate(self.json())


__all__ = ["HTTPError", "Headers", "Request"]
