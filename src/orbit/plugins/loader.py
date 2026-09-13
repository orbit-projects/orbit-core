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
"""Explicit allowlisted discovery; imports are an execution trust boundary."""

from collections.abc import Collection
from importlib.metadata import entry_points

from orbit.errors import ErrorCategory, OrbitProblem, PluginError
from orbit.plugins.contracts import PluginContract

ENTRY_POINT_GROUP = "orbit.plugins"


def discover_plugins(*, allow: Collection[str]) -> tuple[PluginContract, ...]:
    """Load only named entry points, after validating duplicates and missing entries.

    No entry points are imported until their names match the caller's allowlist.
    Allowlisting is authorization to execute installed plugin code, not sandboxing.
    """
    selected = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name in allow]
    names = [ep.name for ep in selected]
    if len(names) != len(set(names)) or set(names) != set(allow):
        raise PluginError(
            OrbitProblem(
                code="plugin.discovery",
                message="Requested plugin entry points are missing or ambiguous.",
                category=ErrorCategory.PLUGIN,
            )
        )
    plugins: list[PluginContract] = []
    for entry in sorted(selected, key=lambda ep: ep.name):
        try:
            loaded = entry.load()
            plugin = loaded() if callable(loaded) else loaded
            if not isinstance(plugin, PluginContract):
                raise TypeError("Entry point does not implement PluginContract.")
        except Exception as exc:
            raise PluginError(
                OrbitProblem(
                    code="plugin.load-failed",
                    message=f"Unable to load plugin entry point {entry.name}.",
                    category=ErrorCategory.PLUGIN,
                )
            ) from exc
        plugins.append(plugin)
    return tuple(plugins)


__all__ = ["ENTRY_POINT_GROUP", "discover_plugins"]
