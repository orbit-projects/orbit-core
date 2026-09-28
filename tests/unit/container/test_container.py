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
"""Dependency container behavior tests."""

import pytest

from orbit.container import Container, Provider, ProviderResolution, Scope
from orbit.errors import ContainerError
from orbit.types import new_provider_id


def test_container_caches_singletons() -> None:
    """A singleton factory runs only once across repeated resolutions."""
    container = Container()
    created: list[object] = []
    container.register_factory("value", lambda _: created.append(object()) or created[-1])

    assert container.resolve("value") is container.resolve("value")
    assert len(created) == 1


def test_provider_definitions_have_stable_typed_ids() -> None:
    """Provider identities remain stable while distinct registrations do not collide."""
    container = Container()
    container.register_factory("first", lambda _: object())
    container.register_factory("second", lambda _: object())

    providers = container.providers
    assert providers["first"].id == container.providers["first"].id
    assert providers["first"].id != providers["second"].id


def test_provider_records_validate_direct_construction() -> None:
    provider = Provider(lambda _: object(), Scope.SINGLETON, ("dependency",), id=new_provider_id())
    assert provider.dependencies == ("dependency",)
    with pytest.raises(TypeError, match="callable"):
        Provider(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Scope"):
        Provider(lambda _: object(), "singleton")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="bounded"):
        Provider(lambda _: object(), dependencies=("",))
    with pytest.raises(ValueError, match="control"):
        Provider(lambda _: object(), dependencies=("dependency\nname",))
    with pytest.raises(TypeError, match="boolean"):
        Provider(lambda _: object(), resource=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="UUID"):
        Provider(lambda _: object(), id="provider")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="dependencies"):
        Provider(lambda _: object(), dependencies=tuple(f"dependency-{i}" for i in range(1_025)))


def test_provider_resolution_records_validate_diagnostic_boundaries() -> None:
    record = ProviderResolution("database", Scope.SINGLETON, True, 0.25)
    assert record.elapsed_seconds == 0.25
    with pytest.raises(TypeError, match="boolean"):
        ProviderResolution("database", Scope.SINGLETON, 1, 0.25)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="elapsed"):
        ProviderResolution("database", Scope.SINGLETON, True, float("nan"))
    with pytest.raises(ValueError, match="error types"):
        ProviderResolution("database", Scope.SINGLETON, False, 0, error_type="bad\nerror")
    with pytest.raises(ValueError, match="control"):
        ProviderResolution("database\tname", Scope.SINGLETON, True, 0.25)


def test_provider_resolution_sanitizes_unsafe_exception_type_names() -> None:
    """A provider failure still produces a valid observer record with hostile class metadata."""
    unsafe_error = type("private\n" + "x" * 128, (RuntimeError,), {})
    container = Container()
    records: list[ProviderResolution] = []
    container.observe(records.append)

    def fail(_: Container) -> None:
        raise unsafe_error("private provider detail")

    container.register_factory("provider", fail)
    with pytest.raises(unsafe_error):
        container.resolve("provider")

    assert records[-1].success is False
    assert records[-1].error_type == "Exception"


def test_container_reports_circular_dependencies() -> None:
    """Circular factory resolution produces an actionable structured exception."""
    container = Container()
    container.register_factory("a", lambda current: current.resolve("b"))
    container.register_factory("b", lambda current: current.resolve("a"))

    with pytest.raises(ContainerError, match="Circular dependency"):
        container.resolve("a")


def test_container_validates_provider_contracts_before_registration() -> None:
    container = Container()
    with pytest.raises(TypeError, match="keys"):
        container.register_factory(42, lambda _: object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="nonempty"):
        container.register_factory("", lambda _: object())
    with pytest.raises(ValueError, match="control"):
        container.register_factory("dependency\nname", lambda _: object())
    with pytest.raises(TypeError, match="callable"):
        container.register_factory("factory", object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Scope"):
        container.register_factory("scope", lambda _: object(), scope="singleton")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="tuple"):
        container.register_factory("dependencies", lambda _: object(), dependencies=["other"])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="dependencies"):
        container.register_factory(
            "too-many-dependencies",
            lambda _: object(),
            dependencies=tuple(f"dependency-{i}" for i in range(1_025)),
        )
    with pytest.raises(TypeError, match="boolean"):
        container.register_factory("replace", lambda _: object(), replace=1)  # type: ignore[arg-type]


def test_container_provider_capacity_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The root dependency graph cannot grow beyond Core's shared capacity."""
    monkeypatch.setattr("orbit.container.container._MAX_CORE_CAPACITY", 1)
    container = Container()
    container.register_factory("first", lambda _: object())
    with pytest.raises(ContainerError, match="capacity"):
        container.register_factory("second", lambda _: object())
    container.register_factory("first", lambda _: object(), replace=True)


def test_container_validates_resolution_keys() -> None:
    container = Container()
    with pytest.raises(TypeError, match="keys"):
        container.resolve([])  # type: ignore[arg-type]
