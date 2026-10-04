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
import inspect
import re
from graphlib import CycleError, TopologicalSorter
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError as PydanticValidationError

from orbit._limits import _MAX_CORE_CAPACITY, is_finite_number
from orbit.errors import ErrorCategory, OrbitProblem, PluginError
from orbit.plugins.contracts import PluginContract
from orbit.plugins.metadata import CORE_API_VERSION, PluginMetadata

if TYPE_CHECKING:
    from orbit.application import Application

_PLUGIN_NAME = re.compile(r"orbit-[a-z][a-z0-9-]{0,62}")
_CAPABILITY_NAME = re.compile(r"[a-z][a-z0-9_.-]{0,62}")


def _error(code: str, message: str) -> PluginError:
    return PluginError(
        OrbitProblem(code="plugin." + code, message=message, category=ErrorCategory.PLUGIN)
    )


class PluginRegistry:
    """Own trusted plugin definitions and the exact set requiring cleanup."""

    def __init__(self) -> None:
        self._plugins: dict[str, PluginContract] = {}
        self._metadata: dict[str, PluginMetadata] = {}
        self._plugin_ids: set[object] = set()
        self._registered_names: dict[int, str] = {}
        self._active: list[PluginContract] = []
        self._enabled: set[str] = set()
        self._frozen = False
        self._detached_hooks: dict[tuple[str, str], asyncio.Future[Any]] = {}

    @property
    def plugins(self) -> tuple[PluginContract, ...]:
        """Return plugins in registration order."""
        return tuple(self._plugins.values())

    @property
    def active_names(self) -> tuple[str, ...]:
        """Return successfully entered activation hooks, in dependency order."""
        # Cleanup must use the trusted registration snapshot. A plugin hook can replace its
        # live metadata object, but that must not change the identity used for bookkeeping.
        return tuple(self._snapshot_for(plugin).name for plugin in self._active)

    @property
    def enabled_names(self) -> tuple[str, ...]:
        """Return enabled plugin names in registration order."""
        return tuple(name for name in self._plugins if name in self._enabled)

    def disable(self, name: str) -> None:
        """Disable a plugin before composition freezes."""
        self._validate_name(name)
        if self._frozen:
            raise _error("frozen", "Plugin enablement is closed.")
        if name not in self._plugins:
            raise KeyError(name)
        self._enabled.discard(name)

    def enable(self, name: str) -> None:
        """Enable a registered plugin before composition freezes."""
        self._validate_name(name)
        if self._frozen:
            raise _error("frozen", "Plugin enablement is closed.")
        if name not in self._plugins:
            raise KeyError(name)
        self._enabled.add(name)

    def with_capability(self, capability: str) -> tuple[PluginContract, ...]:
        """Return registered plugins advertising a capability in deterministic order."""
        if not isinstance(capability, str) or _CAPABILITY_NAME.fullmatch(capability) is None:
            raise ValueError("Plugin capabilities must be bounded lowercase identifiers.")
        return tuple(
            self._plugins[name]
            for name, metadata in self._metadata.items()
            if name in self._enabled and capability in metadata.capabilities
        )

    def snapshot_for(self, plugin: PluginContract) -> PluginMetadata:
        """Return a registered plugin's frozen metadata for trusted inspection and cleanup.

        This method intentionally does not read the plugin's live ``metadata`` attribute. Core
        must retain a stable identity while reporting state or rolling back a hook that changed
        its own metadata object.
        """
        return self._snapshot_for(plugin)

    def register(self, plugin: PluginContract) -> PluginMetadata:
        """Validate and freeze plugin identity before executing plugin code.

        The returned metadata is the detached Core snapshot used for composition and rollback.
        Plugin implementations may expose a mutable attribute even though the metadata model
        itself is frozen, so later lifecycle code must never use that attribute as an identity
        source without checking it against this snapshot.
        """
        if self._frozen:
            raise _error("frozen", "Plugin registration is closed.")
        if len(self._plugins) >= _MAX_CORE_CAPACITY:
            raise _error("capacity", "Plugin registry capacity reached.")
        if not isinstance(plugin, PluginContract):
            raise _error("invalid-contract", "Object does not implement PluginContract.")
        if not callable(plugin.activate) or not callable(plugin.deactivate):
            raise _error("invalid-contract", "Plugin lifecycle hooks must be callable.")
        setup = getattr(plugin, "setup", None)
        if setup is not None and not callable(setup):
            raise _error("invalid-contract", "Plugin setup hook must be callable.")
        if setup is not None and inspect.iscoroutinefunction(setup):
            raise _error("invalid-contract", "Plugin setup hooks must be synchronous.")
        try:
            meta = PluginMetadata.model_validate(plugin.metadata)
        except PydanticValidationError as exc:
            raise _error("invalid-metadata", "Plugin metadata failed Core validation.") from exc
        if meta.api_version != CORE_API_VERSION:
            raise _error("incompatible-api", f"Unsupported plugin API {meta.api_version}.")
        if meta.name in self._plugins or meta.id in self._plugin_ids:
            raise _error("duplicate-plugin", f"Duplicate plugin: {meta.name}.")
        self._plugins[meta.name] = plugin
        self._metadata[meta.name] = meta
        self._plugin_ids.add(meta.id)
        self._registered_names[id(plugin)] = meta.name
        self._enabled.add(meta.name)
        return meta

    def ordered(self) -> tuple[PluginContract, ...]:
        """Validate plugin dependencies and return deterministic activation order."""
        # Validate every registered plugin, including disabled ones. A post-registration identity
        # change otherwise could make diagnostics and enablement refer to a different plugin.
        for name, plugin in self._plugins.items():
            self._stable_metadata(name, plugin)
        enabled = set(self._enabled)
        graph = {
            name: tuple(
                dependency
                for dependency in (*meta.dependencies, *meta.optional_dependencies)
                if dependency in enabled
            )
            for name, meta in self._metadata.items()
            if name in enabled
        }
        available_capabilities = {
            capability
            for candidate, candidate_meta in self._metadata.items()
            if candidate in enabled
            for capability in candidate_meta.capabilities
        }
        for name in enabled:
            metadata = self._metadata[name]
            missing = set(metadata.dependencies) - enabled
            if missing:
                raise _error("missing-dependency", f"{name} requires {missing}.")
            missing_capabilities = set(metadata.required_capabilities) - available_capabilities
            if missing_capabilities:
                raise _error(
                    "missing-capability",
                    f"{name} requires capabilities {sorted(missing_capabilities)}.",
                )
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
        """Run composition hooks once and permanently freeze plugin composition.

        ``setup(application)`` is optional: lifecycle-only plugins still participate in
        dependency ordering and activation. Setup is not retryable because hooks may have
        side effects; if one fails, the registry remains frozen. Plugin code is trusted,
        not sandboxed.
        """
        if self._frozen:
            raise _error("frozen", "Plugin composition has already been frozen.")
        ordered = self.ordered()
        self._frozen = True
        for plugin in ordered:
            setup = getattr(plugin, "setup", None)
            if setup is not None:
                outcome = setup(application)
                if inspect.isawaitable(outcome):
                    # A regular function can still return async work. Stop it before failing:
                    # close native coroutines and cancel loop-owned futures/tasks, consuming
                    # their eventual result so invalid setup cannot leak work or warnings.
                    if inspect.iscoroutine(outcome):
                        outcome.close()
                    elif isinstance(outcome, asyncio.Future):
                        outcome.cancel()
                        outcome.add_done_callback(_consume_future_result)
                    raise _error("invalid-setup", "Plugin setup hooks must be synchronous.")
                self._stable_metadata(self._registered_name(plugin), plugin)

    async def activate(self, timeout: float = 30) -> None:
        """Activate dependencies first; record partial activation for rollback.

        A hook that suppresses cancellation is detached at the deadline and retained by
        plugin identity and phase. This keeps startup bounded while preventing cleanup from
        concurrently invoking ``deactivate`` for a still-running activation hook.
        """
        self._validate_timeout(timeout)
        if self._active:
            raise _error("already-active", "Plugins are already activated.")
        for plugin in self.ordered():
            self._active.append(plugin)
            await self._await_hook(plugin, "activate", timeout)

    async def deactivate(self, timeout: float = 30) -> None:
        """Attempt every deactivation and aggregate errors without skipping dependencies.

        If an activation hook is still cancelling, deactivation fails closed for that plugin
        rather than running two lifecycle hooks concurrently. A cancellation-resistant
        deactivation hook is similarly detached and retained until it actually exits.
        """
        self._validate_timeout(timeout)
        failures: list[Exception] = []
        while self._active:
            plugin = self._active.pop()
            name = self._snapshot_for(plugin).name
            activation_key = (name, "activate")
            if not self._hook_available(activation_key):
                failures.append(
                    RuntimeError(
                        f"Plugin {name!r} activation is still cancelling; deactivation was skipped."
                    )
                )
                continue
            try:
                # Cleanup deliberately uses the frozen name and does not revalidate metadata
                # after the hook: rollback must remain possible after a failed activation hook.
                await self._await_hook(plugin, "deactivate", timeout, verify_metadata=False)
            except (Exception, asyncio.CancelledError) as exc:
                failures.append(
                    exc if isinstance(exc, Exception) else RuntimeError("Plugin cleanup cancelled.")
                )
        if failures:
            raise ExceptionGroup("Plugin cleanup failed", failures)

    def cancel_pending(self) -> None:
        """Request cancellation of lifecycle hooks that outlived their deadline."""
        for task in tuple(self._detached_hooks.values()):
            task.cancel()

    async def _await_hook(
        self,
        plugin: PluginContract,
        phase: str,
        timeout: float,
        *,
        verify_metadata: bool = True,
    ) -> None:
        """Run one plugin hook under a deadline without awaiting cancellation-resistant code."""
        name = self._registered_name(plugin)
        key = (name, phase)
        if not self._hook_available(key):
            raise RuntimeError(f"Plugin {name!r} {phase} hook is still cancelling.")
        hook = getattr(plugin, phase)
        outcome = hook()
        if not inspect.isawaitable(outcome):
            raise TypeError(f"Plugin {phase} hooks must return awaitables.")
        task = asyncio.ensure_future(outcome)
        try:
            done, _ = await asyncio.wait({task}, timeout=timeout)
            if not done:
                self._detach_hook(key, task)
                raise TimeoutError(f"Plugin {name!r} {phase} exceeded its deadline.")
            task.result()
            if verify_metadata:
                self._stable_metadata(name, plugin)
        except asyncio.CancelledError:
            self._detach_hook(key, task)
            raise

    def _registered_name(self, plugin: PluginContract) -> str:
        """Return a plugin's trusted registry name without consulting live metadata."""
        name = self._registered_names.get(id(plugin))
        if name is not None and self._plugins.get(name) is plugin:
            return name
        raise _error("unknown-plugin", "Plugin is not registered with this registry.")

    def _snapshot_for(self, plugin: PluginContract) -> PluginMetadata:
        """Return frozen metadata for cleanup paths that must survive hook failures."""
        return self._metadata[self._registered_name(plugin)]

    def _stable_metadata(self, name: str, plugin: PluginContract) -> PluginMetadata:
        """Reject live metadata that no longer matches its frozen registration snapshot."""
        expected = self._metadata[name]
        try:
            current = PluginMetadata.model_validate(plugin.metadata)
        except Exception as exc:
            raise _error(
                "metadata-mutated",
                "A registered plugin metadata object is no longer valid.",
            ) from exc
        if current != expected:
            raise _error(
                "metadata-mutated",
                "A registered plugin metadata object changed after registration.",
            )
        return expected

    def _hook_available(self, key: tuple[str, str]) -> bool:
        """Return whether a lifecycle phase may run without overlapping detached work."""
        task = self._detached_hooks.get(key)
        if task is None:
            return True
        if task.done():
            self._retire_hook(key, task)
            return True
        return False

    def _detach_hook(self, key: tuple[str, str], task: asyncio.Future[Any]) -> None:
        """Cancel and retain one timed-out hook until its provider code actually exits."""
        self._detached_hooks[key] = task
        task.cancel()
        task.add_done_callback(lambda finished: self._retire_hook(key, finished))

    def _retire_hook(self, key: tuple[str, str], task: asyncio.Future[Any]) -> None:
        """Forget a detached hook and consume late failures without loop warnings."""
        if self._detached_hooks.get(key) is task:
            del self._detached_hooks[key]
        _consume_future_result(task)

    @staticmethod
    def _validate_name(name: str) -> None:
        """Validate an operation name before using it as a registry key."""
        if not isinstance(name, str):
            raise TypeError("Plugin names must be strings.")
        if _PLUGIN_NAME.fullmatch(name) is None:
            raise ValueError("Plugin names must be bounded orbit plugin identifiers.")

    @staticmethod
    def _validate_timeout(timeout: float) -> None:
        """Reject invalid lifecycle deadlines before entering asyncio timeout scopes."""
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not is_finite_number(timeout)
            or timeout <= 0
        ):
            raise ValueError("Plugin timeout must be finite and positive.")


def _consume_future_result(task: asyncio.Future[Any]) -> None:
    """Consume a detached lifecycle hook result without emitting loop-level warnings."""
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        return


__all__ = ["PluginRegistry"]
