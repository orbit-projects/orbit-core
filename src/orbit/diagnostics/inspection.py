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
"""Typed inspection of composition shared by administrative and CLI clients.

Inspection never imports providers, acquires resources, or mutates the application.
It describes the supplied instance, including only explicitly registered components.
Configuration uses Core's redacted JSON representation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator

from orbit._immutability import freeze_mapping, validate_mapping
from orbit._limits import _MAX_CORE_CAPACITY, _MAX_RELATION_ENTRIES
from orbit.container.scope import Scope
from orbit.plugins.metadata import PluginMetadata
from orbit.routing.models import RouteMetadata
from orbit.services.models import ServiceDescriptor
from orbit.types import ConfigurationId, ProviderId

if TYPE_CHECKING:
    from orbit.application import Application


class ProviderDescription(BaseModel):
    """Provider registration metadata without factory objects or resolved values."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)
    id: ProviderId
    key: StrictStr = Field(min_length=1, max_length=255)
    scope: Scope
    dependencies: tuple[StrictStr, ...]
    resource: StrictBool

    @field_validator("key", "dependencies")
    @classmethod
    def validate_text(cls, value: str | tuple[str, ...]) -> str | tuple[str, ...]:
        """Reject control-bearing provider text before it reaches operator output."""
        values = (value,) if isinstance(value, str) else value
        if any(
            not 1 <= len(item) <= 255
            or any(ord(character) < 32 or ord(character) == 127 for character in item)
            for item in values
        ):
            raise ValueError("Provider inspection text must be bounded printable strings.")
        return value

    @field_validator("dependencies")
    @classmethod
    def validate_dependency_count(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Keep provider inspection graphs within the direct dependency edge limit."""
        if len(values) > _MAX_RELATION_ENTRIES:
            raise ValueError(
                f"Provider inspection dependencies cannot contain more than "
                f"{_MAX_RELATION_ENTRIES:,} entries."
            )
        return values


class CompositionSnapshot(BaseModel):
    """Validated, detached metadata describing the currently composed application."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)
    services: tuple[ServiceDescriptor, ...]
    plugins: tuple[PluginMetadata, ...]
    enabled_plugins: tuple[StrictStr, ...] = ()
    routes: tuple[RouteMetadata, ...]
    dependencies: tuple[ProviderDescription, ...]
    configuration_id: ConfigurationId
    configuration: dict[str, Any]

    @field_validator("services", "plugins", "enabled_plugins", "routes", "dependencies")
    @classmethod
    def validate_collection_capacity(cls, values: tuple[object, ...]) -> tuple[object, ...]:
        """Keep detached composition sections bounded before operator serialization."""
        if len(values) > _MAX_CORE_CAPACITY:
            raise ValueError("Composition snapshot collections exceed Core's capacity limit.")
        return values

    @field_validator("configuration", mode="before")
    @classmethod
    def validate_configuration(cls, value: object) -> dict[str, Any]:
        """Bound configuration keys before an inspection snapshot is retained."""
        return validate_mapping(value, name="Composition configuration")

    def model_post_init(self, __context: object) -> None:
        """Freeze nested configuration so a published operator snapshot cannot be rewritten."""
        object.__setattr__(self, "configuration", freeze_mapping(self.configuration))


def inspect_composition(application: Application) -> CompositionSnapshot:
    """Capture component definitions without triggering lifecycle or provider hooks."""
    return CompositionSnapshot(
        services=application.services.descriptors,
        # Composition is an operator-facing snapshot; never expose a live plugin identity that
        # can change after registration and diverge from enablement or lifecycle state.
        plugins=tuple(
            application.plugins.snapshot_for(plugin) for plugin in application.plugins.plugins
        ),
        enabled_plugins=application.plugins.enabled_names,
        routes=tuple(route.metadata for route in application.router.routes),
        dependencies=tuple(
            ProviderDescription(
                id=provider.id,
                key=str(key),
                scope=provider.scope,
                dependencies=tuple(str(dependency) for dependency in provider.dependencies),
                resource=provider.resource,
            )
            for key, provider in application.container.providers.items()
        ),
        configuration_id=application.config.configuration_id,
        configuration=application.config.inspect(),
    )


__all__ = ["CompositionSnapshot", "ProviderDescription", "inspect_composition"]
