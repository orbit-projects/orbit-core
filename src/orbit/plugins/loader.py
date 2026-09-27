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

import re
from collections.abc import Collection
from importlib.metadata import entry_points

from orbit.errors import ErrorCategory, OrbitProblem, PluginError
from orbit.plugins.contracts import PluginContract

ENTRY_POINT_GROUP = "orbit.plugins"
_ENTRY_POINT_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,126}")


def _validate_allowlist(allow: Collection[str]) -> tuple[str, ...]:
    """Materialize the plugin execution allowlist without implicit string coercion."""
    if isinstance(allow, (str, bytes)):
        raise TypeError("Plugin allowlists must be collections of names, not scalar text.")
    try:
        names = tuple(allow)
    except TypeError as exc:
        raise TypeError("Plugin allowlists must be collections of names.") from exc
    if any(not isinstance(name, str) for name in names):
        raise TypeError("Plugin allowlists must contain only strings.")
    if any(_ENTRY_POINT_NAME.fullmatch(name) is None for name in names):
        raise ValueError("Plugin allowlist names must be bounded lowercase identifiers.")
    if len(names) != len(set(names)):
        raise ValueError("Plugin allowlists must not contain duplicate names.")
    return names


def discover_plugins(*, allow: Collection[str]) -> tuple[PluginContract, ...]:
    """Load only named entry points after validating the execution allowlist and entries.

    No package metadata is consulted for an empty allowlist, and no entry points are imported
    until their names match the caller's validated allowlist. Allowlisting is authorization to
    execute installed plugin code, not sandboxing.
    """
    allowlist = _validate_allowlist(allow)
    if not allowlist:
        return ()
    selected = [ep for ep in entry_points(group=ENTRY_POINT_GROUP) if ep.name in allowlist]
    names = [ep.name for ep in selected]
    if len(names) != len(set(names)) or set(names) != set(allowlist):
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
