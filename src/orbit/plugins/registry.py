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
"""Deterministic plugin composition with failure-safe activation and cleanup."""

from __future__ import annotations

import asyncio
from graphlib import CycleError, TopologicalSorter
from typing import TYPE_CHECKING

from orbit.errors import ErrorCategory, OrbitProblem, PluginError
from orbit.plugins.contracts import PluginContract
from orbit.plugins.metadata import PluginMetadata

if TYPE_CHECKING:
    from orbit.application import Application


def _error(code: str, message: str) -> PluginError:
    return PluginError(
        OrbitProblem(code="plugin." + code, message=message, category=ErrorCategory.PLUGIN)
    )


class PluginRegistry:
    """Own trusted plugin definitions and the exact set requiring cleanup."""

    def __init__(self) -> None:
        self._plugins: dict[str, PluginContract] = {}
        self._metadata: dict[str, PluginMetadata] = {}
        self._active: list[PluginContract] = []
        self._frozen = False

    @property
    def plugins(self) -> tuple[PluginContract, ...]:
        """Return plugins in registration order."""
        return tuple(self._plugins.values())

    @property
    def active_names(self) -> tuple[str, ...]:
        """Return successfully entered activation hooks, in dependency order."""
        return tuple(p.metadata.name for p in self._active)

    def register(self, plugin: PluginContract) -> None:
        """Reject invalid API versions and duplicate identities before executing plugin code."""
        if self._frozen:
            raise _error("frozen", "Plugin registration is closed.")
        if not isinstance(plugin, PluginContract):
            raise _error("invalid-contract", "Object does not implement PluginContract.")
        meta = PluginMetadata.model_validate(plugin.metadata)
        if meta.api_version != "0.1":
            raise _error("incompatible-api", f"Unsupported plugin API {meta.api_version}.")
        if meta.name in self._plugins or meta.id in {m.id for m in self._metadata.values()}:
            raise _error("duplicate-plugin", f"Duplicate plugin: {meta.name}.")
        self._plugins[meta.name] = plugin
        self._metadata[meta.name] = meta

    def ordered(self) -> tuple[PluginContract, ...]:
        """Validate plugin dependencies and return deterministic activation order."""
        graph = {name: meta.dependencies for name, meta in self._metadata.items()}
        for name, dependencies in graph.items():
            missing = set(dependencies) - graph.keys()
            if missing:
                raise _error("missing-dependency", f"{name} requires {missing}.")
        try:
            names = tuple(TopologicalSorter(graph).static_order())
        except CycleError as exc:
            raise _error("dependency-cycle", f"Plugin cycle: {exc.args[1]}") from exc
        return tuple(self._plugins[name] for name in names)

    def setup(self, application: Application) -> None:
        """Execute composition hooks once; plugin code is trusted, not sandboxed."""
        ordered = self.ordered()
        self._frozen = True
        for plugin in ordered:
            setup = getattr(plugin, "setup", None)
            if setup is not None:
                setup(application)

    async def activate(self, timeout: float = 30) -> None:
        """Activate dependencies first; record partial activation for rollback."""
        if self._active:
            raise _error("already-active", "Plugins are already activated.")
        for plugin in self.ordered():
            self._active.append(plugin)
            async with asyncio.timeout(timeout):
                await plugin.activate()

    async def deactivate(self, timeout: float = 30) -> None:
        """Attempt every deactivation and aggregate errors without skipping dependencies."""
        failures: list[Exception] = []
        while self._active:
            plugin = self._active.pop()
            try:
                async with asyncio.timeout(timeout):
                    await plugin.deactivate()
            except (Exception, asyncio.CancelledError) as exc:
                failures.append(
                    exc if isinstance(exc, Exception) else RuntimeError("Plugin cleanup cancelled.")
                )
        if failures:
            raise ExceptionGroup("Plugin cleanup failed", failures)


__all__ = ["PluginRegistry"]
