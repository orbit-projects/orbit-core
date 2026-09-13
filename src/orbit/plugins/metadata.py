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

from pydantic import BaseModel, ConfigDict, Field

from orbit.types import PluginId, new_plugin_id


class PluginMetadata(BaseModel):
    """Metadata for a trusted Python plugin or native implementation's Python binding."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    id: PluginId = Field(default_factory=new_plugin_id)
    name: str = Field(pattern=r"^orbit-[a-z][a-z0-9-]{0,62}$")
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+(?:[a-z0-9.+-]+)?$")
    api_version: str = "0.1"
    dependencies: tuple[str, ...] = ()
    capabilities: frozenset[str] = frozenset()


__all__ = ["PluginMetadata"]
