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
"""Portable error model shared by exceptions, ASGI, and diagnostics."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orbit.errors.categories import ErrorCategory, ErrorSeverity


class OrbitProblem(BaseModel):
    """A serializable representation of an operational failure."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^[a-z][a-z0-9.-]{0,126}$")
    message: str
    category: ErrorCategory
    severity: ErrorSeverity = ErrorSeverity.ERROR
    context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    cause: str | None = None


__all__ = ["OrbitProblem"]
