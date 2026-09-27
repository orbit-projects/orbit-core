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
"""Validated HTTP route metadata and authorization requirements."""

import re

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from orbit._limits import MAX_PATH_BYTES, MAX_PATH_PARAMETER_NAME_LENGTH, MAX_PATH_PARAMETERS
from orbit.security.roles import validate_role_collection
from orbit.types import RouteId, ServiceId, new_route_id


class RouteMetadata(BaseModel):
    """Static route identity, service ownership, method and optional required roles."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)
    id: RouteId = Field(default_factory=new_route_id)
    name: StrictStr = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    path: StrictStr = Field(max_length=2048)
    method: StrictStr
    service_id: ServiceId | None = None
    roles: frozenset[StrictStr] = frozenset()
    summary: StrictStr = Field(default="", max_length=1024)
    api_version: StrictStr | None = Field(default=None, max_length=32, pattern=r"^v[0-9]+$")

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        """Normalize supported HTTP methods and reject accidental protocol extensions."""
        value = value.upper()
        if value not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
            raise ValueError("Unsupported HTTP method.")
        return value

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, value: frozenset[str]) -> frozenset[str]:
        """Reject unsafe role text before route metadata reaches OpenAPI or dispatch."""
        return validate_role_collection(value)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        """Keep OpenAPI and administrative route summaries printable and bounded."""
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Route summaries must be printable text.")
        return value

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        """Require absolute paths and whole-segment, uniquely named parameters."""
        try:
            path_size = len(value.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError("Route paths must contain valid Unicode text.") from exc
        if path_size > MAX_PATH_BYTES:
            raise ValueError("Route paths exceed the maximum supported length.")
        if not value.startswith("/") or any(c in value for c in "?#\\") or "//" in value:
            raise ValueError("Route requires an absolute canonical path.")
        names: set[str] = set()
        parameter_count = 0
        for segment in value.split("/"):
            if segment in {".", ".."} or any(ord(c) < 32 or ord(c) == 127 for c in segment):
                raise ValueError("Invalid path segment.")
            if "{" in segment or "}" in segment:
                if not re.fullmatch(
                    rf"\{{[a-zA-Z_][a-zA-Z0-9_]{{0,{MAX_PATH_PARAMETER_NAME_LENGTH - 1}}}\}}",
                    segment,
                ):
                    raise ValueError("Parameters must occupy one complete path segment.")
                if segment in names:
                    raise ValueError("Duplicate path parameter.")
                names.add(segment)
                parameter_count += 1
                if parameter_count > MAX_PATH_PARAMETERS:
                    raise ValueError("Route path parameters exceed the safety limit.")
        return value


__all__ = ["RouteMetadata"]
