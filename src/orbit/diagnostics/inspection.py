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

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from orbit.container.scope import Scope
from orbit.plugins.metadata import PluginMetadata
from orbit.routing.models import RouteMetadata
from orbit.services.models import ServiceDescriptor

if TYPE_CHECKING:
    from orbit.application import Application


class ProviderDescription(BaseModel):
    """Provider registration metadata without factory objects or resolved values."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    key: str
    scope: Scope
    dependencies: tuple[str, ...]
    resource: bool


class CompositionSnapshot(BaseModel):
    """Detached metadata describing the currently composed application."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    services: tuple[ServiceDescriptor, ...]
    plugins: tuple[PluginMetadata, ...]
    routes: tuple[RouteMetadata, ...]
    dependencies: tuple[ProviderDescription, ...]
    configuration: dict[str, object]


def inspect_composition(application: Application) -> CompositionSnapshot:
    """Capture component definitions without triggering lifecycle or provider hooks."""
    return CompositionSnapshot(
        services=application.services.descriptors,
        plugins=tuple(plugin.metadata for plugin in application.plugins.plugins),
        routes=tuple(route.metadata for route in application.router.routes),
        dependencies=tuple(
            ProviderDescription(
                key=str(key),
                scope=provider.scope,
                dependencies=tuple(str(dependency) for dependency in provider.dependencies),
                resource=provider.resource,
            )
            for key, provider in application.container.providers.items()
        ),
        configuration=application.config.inspect(),
    )


__all__ = ["CompositionSnapshot", "ProviderDescription", "inspect_composition"]
