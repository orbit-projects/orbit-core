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

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orbit.types import RouteId, ServiceId, new_route_id


class RouteMetadata(BaseModel):
    """Static route identity, service ownership, method and optional required roles."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: RouteId = Field(default_factory=new_route_id)
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    path: str
    method: str
    service_id: ServiceId | None = None
    roles: frozenset[str] = frozenset()
    summary: str = ""

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        """Normalize supported HTTP methods and reject accidental protocol extensions."""
        value = value.upper()
        if value not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
            raise ValueError("Unsupported HTTP method.")
        return value

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        """Require absolute paths and whole-segment, uniquely named parameters."""
        if not value.startswith("/") or any(c in value for c in "?#\\") or "//" in value:
            raise ValueError("Route requires an absolute canonical path.")
        names: set[str] = set()
        for segment in value.split("/"):
            if segment in {".", ".."} or any(ord(c) < 32 for c in segment):
                raise ValueError("Invalid path segment.")
            if "{" in segment or "}" in segment:
                if not re.fullmatch(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", segment):
                    raise ValueError("Parameters must occupy one complete path segment.")
                if segment in names:
                    raise ValueError("Duplicate path parameter.")
                names.add(segment)
        return value


__all__ = ["RouteMetadata"]
