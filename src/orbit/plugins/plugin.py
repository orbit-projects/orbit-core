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
"""Optional plugin hooks for composition, activation and cleanup."""

from __future__ import annotations

from typing import TYPE_CHECKING

from orbit.plugins.metadata import PluginMetadata

if TYPE_CHECKING:
    from orbit.application import Application


class Plugin:
    """Extend Core through explicit composition; concrete provider logic stays outside Core."""

    metadata: PluginMetadata

    def setup(self, application: Application) -> None:
        """Register services, providers, routes and subscriptions before composition freezes."""

    async def activate(self) -> None:
        """Acquire plugin resources before service initialization."""

    async def deactivate(self) -> None:
        """Release resources, including partial activation; must tolerate incomplete startup."""


__all__ = ["Plugin"]
