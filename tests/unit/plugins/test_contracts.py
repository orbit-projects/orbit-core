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
"""Provider-neutral plugin runtime contract suite; no external providers are needed."""

import asyncio
from types import SimpleNamespace

import pytest

from orbit import Application, ApplicationConfig
from orbit.errors import PluginError
from orbit.plugins import CORE_API_VERSION, Plugin, PluginMetadata, PluginRegistry
from orbit.plugins.loader import discover_plugins


class Extension(Plugin):
    def __init__(
        self,
        name="orbit-extension",
        dependencies=(),
        api_version=CORE_API_VERSION,
        optional_dependencies=(),
        capabilities=(),
        required_capabilities=(),
    ):
        self.metadata = PluginMetadata(
            name=name,
            version="1.0.0",
            dependencies=dependencies,
            optional_dependencies=optional_dependencies,
            capabilities=frozenset(capabilities),
            required_capabilities=frozenset(required_capabilities),
            api_version=api_version,
        )


@pytest.mark.parametrize("invalid", [object(), Extension(api_version="9.0")])
def test_invalid_plugin_rejected_before_activation(invalid):
    with pytest.raises(PluginError):
        PluginRegistry().register(invalid)


def test_plugin_registry_rejects_invalid_metadata_hooks_and_timeouts():
    registry = PluginRegistry()
    malformed = Extension()
    malformed.metadata = object()
    with pytest.raises(PluginError, match="metadata"):
        registry.register(malformed)

    invalid_hook = Extension()
    invalid_hook.activate = object()
    with pytest.raises(PluginError, match="hooks"):
        registry.register(invalid_hook)

    with pytest.raises(TypeError, match="names"):
        registry.enable(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="identifiers"):
        registry.with_capability("")
    with pytest.raises(ValueError, match="plugin identifiers"):
        registry.enable("unsafe/name")
    with pytest.raises(ValueError, match="identifiers"):
        registry.with_capability("unsafe capability")


def test_plugin_metadata_validates_direct_contract_fields() -> None:
    """Plugin metadata fails closed before registry composition or plugin code runs."""
    assert PluginMetadata(name="orbit-valid", version="1.0.0").api_version == CORE_API_VERSION
    with pytest.raises(ValueError):
        PluginMetadata(name=1, version="1.0.0")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        PluginMetadata(name="orbit-valid", version=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="dependencies"):
        PluginMetadata(
            name="orbit-valid",
            version="1.0.0",
            dependencies=("not-a-plugin",),
        )
    with pytest.raises(ValueError, match="both required and optional"):
        PluginMetadata(
            name="orbit-valid",
            version="1.0.0",
            dependencies=("orbit-base",),
            optional_dependencies=("orbit-base",),
        )
    with pytest.raises(ValueError, match="capabilities"):
        PluginMetadata(
            name="orbit-valid",
            version="1.0.0",
            capabilities=frozenset({"Not Valid"}),
        )
    with pytest.raises(ValueError):
        PluginMetadata(name="orbit-valid", version="1.0.0-" + "x" * 250)


def test_plugin_registry_capacity_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plugin composition cannot grow an unbounded registry before freezing."""
    monkeypatch.setattr("orbit.plugins.registry._MAX_CORE_CAPACITY", 1)
    registry = PluginRegistry()
    registry.register(Extension("orbit-first"))
    with pytest.raises(PluginError, match="capacity"):
        registry.register(Extension("orbit-second"))


@pytest.mark.asyncio
async def test_plugin_registry_rejects_invalid_lifecycle_timeout():
    registry = PluginRegistry()
    registry.register(Extension())
    with pytest.raises(ValueError, match="timeout"):
        await registry.activate(timeout="1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timeout"):
        await registry.deactivate(timeout=True)


def test_duplicate_identity_and_dependency_validation():
    registry = PluginRegistry()
    registry.register(Extension())
    with pytest.raises(PluginError):
        registry.register(Extension())
    missing = PluginRegistry()
    missing.register(Extension(dependencies=("orbit-missing",)))
    with pytest.raises(PluginError):
        missing.ordered()
    cyclic = PluginRegistry()
    cyclic.register(Extension("orbit-one", ("orbit-two",)))
    cyclic.register(Extension("orbit-two", ("orbit-one",)))
    with pytest.raises(PluginError):
        cyclic.ordered()


def test_plugin_metadata_mutation_fails_closed_after_registration():
    """Composition uses the frozen plugin identity rather than mutable live metadata."""
    registry = PluginRegistry()
    plugin = Extension()
    registry.register(plugin)
    plugin.metadata = plugin.metadata.model_copy(update={"name": "orbit-renamed"})

    with pytest.raises(PluginError, match="changed after registration"):
        registry.ordered()


async def test_plugin_metadata_mutation_does_not_block_snapshot_based_cleanup():
    """A failed activation can still be rolled back using its trusted registration name."""
    calls = []

    class Mutating(Extension):
        async def activate(self):
            self.metadata = self.metadata.model_copy(update={"name": "orbit-renamed"})

        async def deactivate(self):
            calls.append("deactivated")

    registry = PluginRegistry()
    registry.register(Mutating())
    with pytest.raises(PluginError, match="changed after registration"):
        await registry.activate()

    await registry.deactivate()
    assert calls == ["deactivated"]
    assert not registry.active_names


def test_optional_dependencies_and_capability_discovery():
    registry = PluginRegistry()
    optional = Extension(
        "orbit-optional", optional_dependencies=("orbit-missing",), capabilities=("cache",)
    )
    registry.register(optional)
    registry.register(Extension("orbit-consumer", optional_dependencies=("orbit-optional",)))
    assert registry.ordered()[0] is optional
    assert registry.with_capability("cache") == (optional,)


def test_plugin_enablement_and_required_dependencies():
    registry = PluginRegistry()
    base = Extension("orbit-base")
    dependent = Extension("orbit-dependent", dependencies=("orbit-base",))
    registry.register(base)
    registry.register(dependent)
    registry.disable("orbit-base")
    with pytest.raises(PluginError, match="requires"):
        registry.ordered()
    registry.enable("orbit-base")
    assert registry.enabled_names == ("orbit-base", "orbit-dependent")
    registry.disable("orbit-dependent")
    assert registry.ordered() == (base,)
    with pytest.raises(KeyError):
        registry.enable("orbit-missing")


def test_plugin_required_capabilities_are_validated() -> None:
    registry = PluginRegistry()
    registry.register(Extension("orbit-consumer", required_capabilities=("telemetry",)))
    with pytest.raises(PluginError, match="capabilities"):
        registry.ordered()
    registry.register(Extension("orbit-telemetry", capabilities=("telemetry",)))
    assert [plugin.metadata.name for plugin in registry.ordered()] == [
        "orbit-consumer",
        "orbit-telemetry",
    ]


async def test_plugin_ordering_and_cleanup_aggregation():
    calls = []

    class Recorded(Extension):
        async def activate(self):
            calls.append(self.metadata.name)

        async def deactivate(self):
            calls.append("stop:" + self.metadata.name)
            raise ValueError("cleanup")

    registry = PluginRegistry()
    registry.register(Recorded("orbit-dependent", ("orbit-base",)))
    registry.register(Recorded("orbit-base"))
    app = Application(ApplicationConfig(name="plugin-test"))
    registry.setup(app)
    with pytest.raises(PluginError):
        registry.register(Extension("orbit-late"))
    await registry.activate()
    with pytest.raises(PluginError):
        await registry.activate()
    with pytest.raises(ExceptionGroup) as caught:
        await registry.deactivate()
    assert len(caught.value.exceptions) == 2
    assert calls == ["orbit-base", "orbit-dependent", "stop:orbit-dependent", "stop:orbit-base"]
    assert not registry.active_names


async def test_cancellation_resistant_plugin_activation_is_bounded_and_not_reentered():
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    class Stubborn(Extension):
        async def activate(self):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()

        async def deactivate(self):
            calls.append("deactivate")

    registry = PluginRegistry()
    plugin = Stubborn("orbit-stubborn")
    registry.register(plugin)
    activation = asyncio.create_task(registry.activate(timeout=0.01))
    await started.wait()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(activation, timeout=0.1)

    with pytest.raises(ExceptionGroup) as caught:
        await registry.deactivate(timeout=0.01)
    assert "still cancelling" in str(caught.value.exceptions[0])
    assert calls == []

    release.set()
    for _ in range(20):
        await asyncio.sleep(0)
        if not registry._detached_hooks:  # noqa: SLF001 - test-owned cleanup observation.
            break
    assert not registry._detached_hooks  # noqa: SLF001 - verify detached hook retirement.


def test_discovery_does_not_import_unallowlisted_code(monkeypatch):
    calls = []
    safe = SimpleNamespace(name="safe", load=lambda: Extension)

    def forbidden():
        calls.append("executed")
        raise RuntimeError

    unsafe = SimpleNamespace(name="unsafe", load=forbidden)
    monkeypatch.setattr("orbit.plugins.loader.entry_points", lambda **kwargs: [safe, unsafe])
    assert len(discover_plugins(allow={"safe"})) == 1
    assert not calls
    assert discover_plugins(allow=()) == ()
    with pytest.raises(PluginError):
        discover_plugins(allow={"missing"})
    with pytest.raises(PluginError):
        discover_plugins(allow={"unsafe"})


def test_discovery_validates_allowlist_before_entry_point_access(monkeypatch) -> None:
    """Malformed plugin execution policy fails before discovery can import metadata."""
    called = False

    def unexpected_entry_points(**kwargs):
        nonlocal called
        called = True
        return ()

    monkeypatch.setattr("orbit.plugins.loader.entry_points", unexpected_entry_points)
    with pytest.raises(TypeError, match="scalar text"):
        discover_plugins(allow="safe")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="only strings"):
        discover_plugins(allow={"safe", 1})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="lowercase identifiers"):
        discover_plugins(allow={"Unsafe"})
    with pytest.raises(ValueError, match="duplicate"):
        discover_plugins(allow=["safe", "safe"])
    assert discover_plugins(allow=()) == ()
    assert not called


def test_discovery_rejects_duplicate_entries_and_invalid_contract(monkeypatch):
    entry = SimpleNamespace(name="bad", load=lambda: object())
    monkeypatch.setattr("orbit.plugins.loader.entry_points", lambda **kwargs: [entry, entry])
    with pytest.raises(PluginError):
        discover_plugins(allow={"bad"})
    monkeypatch.setattr("orbit.plugins.loader.entry_points", lambda **kwargs: [entry])
    with pytest.raises(PluginError):
        discover_plugins(allow={"bad"})
