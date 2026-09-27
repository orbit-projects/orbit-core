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
"""Versioned plugin contract metadata and declared plugin dependencies."""

import re
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationInfo, field_validator

from orbit.types import PluginId, new_plugin_id

_PLUGIN_NAME = re.compile(r"^orbit-[a-z][a-z0-9-]{0,62}$")
_CAPABILITY_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")

# This is the contract version negotiated by Core, not the package release version. A new
# incompatible extension contract must change this value and include migration guidance.
CORE_API_VERSION: Final[str] = "0.1"


class PluginMetadata(BaseModel):
    """Validated metadata shared by Python and native plugin bindings.

    The model is the Core-side contract boundary: registry validation still checks API
    compatibility and graph availability, but malformed names, dependency declarations, and
    capabilities are rejected before a plugin can enter composition.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)
    id: PluginId = Field(default_factory=new_plugin_id)
    name: StrictStr = Field(pattern=r"^orbit-[a-z][a-z0-9-]{0,62}$")
    version: StrictStr = Field(
        max_length=255,
        pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[a-z0-9.+-]+)?$",
    )
    api_version: StrictStr = Field(default=CORE_API_VERSION, min_length=1, max_length=32)
    dependencies: tuple[StrictStr, ...] = ()
    optional_dependencies: tuple[StrictStr, ...] = ()
    capabilities: frozenset[StrictStr] = frozenset()
    required_capabilities: frozenset[StrictStr] = frozenset()

    @field_validator("dependencies", "optional_dependencies")
    @classmethod
    def validate_dependencies(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Require bounded plugin names and deterministic, duplicate-free declarations."""
        if len(values) > 1_024 or len(values) != len(set(values)):
            raise ValueError("Plugin dependency declarations must be bounded and unique.")
        if any(_PLUGIN_NAME.fullmatch(value) is None for value in values):
            raise ValueError("Plugin dependencies must use valid orbit plugin names.")
        return values

    @field_validator("capabilities", "required_capabilities")
    @classmethod
    def validate_capabilities(cls, values: frozenset[str]) -> frozenset[str]:
        """Require bounded capability identifiers before capability discovery or inspection."""
        if len(values) > 1_024 or any(
            _CAPABILITY_NAME.fullmatch(value) is None for value in values
        ):
            raise ValueError("Plugin capabilities must be bounded lowercase identifiers.")
        return values

    @field_validator("api_version")
    @classmethod
    def validate_api_version(cls, value: str) -> str:
        """Keep the negotiated Core API label bounded and free of control characters."""
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Plugin API versions must be printable text.")
        return value

    @field_validator("optional_dependencies")
    @classmethod
    def reject_duplicate_dependency_classes(
        cls, values: tuple[str, ...], info: ValidationInfo
    ) -> tuple[str, ...]:
        """Reject a plugin declaring the same dependency as both required and optional."""
        required = info.data.get("dependencies", ())
        if set(required).intersection(values):
            raise ValueError("Plugin dependencies cannot be both required and optional.")
        return values


__all__ = ["CORE_API_VERSION", "PluginMetadata"]
