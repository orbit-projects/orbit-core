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
"""Stable plugin contract for Core extension points."""

from typing import Protocol, runtime_checkable

from orbit.plugins.metadata import PluginMetadata


@runtime_checkable
class PluginContract(Protocol):
    """Minimal lifecycle contract for an optional Orbit plugin.

    A plugin may additionally expose ``setup(application)`` to register composition-time
    contributions. That hook is optional and is deliberately not required by this protocol.
    Plugins that want a default setup hook can subclass :class:`orbit.plugins.Plugin`.
    """

    @property
    def metadata(self) -> PluginMetadata:
        """Return validated plugin metadata."""

    async def activate(self) -> None:
        """Activate the plugin after Core initialization."""

    async def deactivate(self) -> None:
        """Deactivate the plugin before application shutdown."""


__all__ = ["PluginContract"]
