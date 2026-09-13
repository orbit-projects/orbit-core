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

from types import SimpleNamespace

import pytest

from orbit import Application, ApplicationConfig
from orbit.errors import PluginError
from orbit.plugins import Plugin, PluginMetadata, PluginRegistry
from orbit.plugins.loader import discover_plugins


class Extension(Plugin):
    def __init__(self, name="orbit-extension", dependencies=(), api_version="0.1"):
        self.metadata = PluginMetadata(
            name=name, version="1.0.0", dependencies=dependencies, api_version=api_version
        )


@pytest.mark.parametrize("invalid", [object(), Extension(api_version="9.0")])
def test_invalid_plugin_rejected_before_activation(invalid):
    with pytest.raises(PluginError):
        PluginRegistry().register(invalid)


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


def test_discovery_rejects_duplicate_entries_and_invalid_contract(monkeypatch):
    entry = SimpleNamespace(name="bad", load=lambda: object())
    monkeypatch.setattr("orbit.plugins.loader.entry_points", lambda **kwargs: [entry, entry])
    with pytest.raises(PluginError):
        discover_plugins(allow={"bad"})
    monkeypatch.setattr("orbit.plugins.loader.entry_points", lambda **kwargs: [entry])
    with pytest.raises(PluginError):
        discover_plugins(allow={"bad"})
