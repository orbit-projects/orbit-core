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
"""Service registration and graph validation independent of lifecycle execution."""

import pytest

from orbit import Service, ServiceDescriptor
from orbit.errors import ValidationError
from orbit.services import ServiceRegistry
from orbit.types import new_service_id


def service(name, dependencies=()):
    instance = Service()
    instance.descriptor = ServiceDescriptor(name=name, dependencies=dependencies)
    return instance


def test_duplicate_name_is_rejected():
    registry = ServiceRegistry()
    registry.register(service("one"))
    with pytest.raises(ValidationError):
        registry.register(service("one"))
    with pytest.raises(ValidationError):
        registry.register(object())


def test_invalid_service_descriptors_and_hooks_use_structured_validation_errors():
    registry = ServiceRegistry()
    malformed = Service()
    malformed.descriptor = {"name": "INVALID"}  # type: ignore[assignment]
    with pytest.raises(ValidationError, match="descriptor"):
        registry.register(malformed)

    invalid_hook = service("invalid-hook")
    invalid_hook.start = object()  # type: ignore[method-assign]
    with pytest.raises(ValidationError, match="hook"):
        registry.register(invalid_hook)


def test_service_descriptor_validates_direct_contract_fields() -> None:
    """Service metadata rejects coercion and unsafe capability labels before registration."""
    with pytest.raises(ValueError):
        ServiceDescriptor(name=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ServiceDescriptor(name="valid", version=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="capabilities"):
        ServiceDescriptor(name="valid", capabilities=frozenset({"Not Valid"}))
    with pytest.raises(ValueError):
        ServiceDescriptor(name="valid", version="1.0.0-" + "x" * 250)


def test_service_dependency_edges_are_bounded() -> None:
    """One service cannot create an unbounded dependency-graph adjacency list."""
    with pytest.raises(ValueError, match="dependencies"):
        ServiceDescriptor(name="valid", dependencies=tuple(new_service_id() for _ in range(1_025)))


def test_service_registry_capacity_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Composition cannot grow an unbounded service registry."""
    monkeypatch.setattr("orbit.services.registry._MAX_CORE_CAPACITY", 1)
    registry = ServiceRegistry()
    registry.register(service("first"))
    with pytest.raises(ValidationError, match="capacity"):
        registry.register(service("second"))


def test_missing_and_cyclic_dependencies():
    registry = ServiceRegistry()
    registry.register(service("missing", (new_service_id(),)))
    with pytest.raises(ValidationError):
        registry.ordered()
    registry = ServiceRegistry()
    a, b = service("one"), service("two")
    a.descriptor = a.descriptor.model_copy(update={"dependencies": (b.descriptor.id,)})
    b.descriptor = b.descriptor.model_copy(update={"dependencies": (a.descriptor.id,)})
    registry.register(a)
    registry.register(b)
    with pytest.raises(ValidationError):
        registry.ordered()
    assert registry.get(a.descriptor.id) is a


def test_service_metadata_and_name_lookup_are_stable():
    registry = ServiceRegistry()
    worker = service("worker")
    worker.descriptor = worker.descriptor.model_copy(
        update={
            "version": "2.1.0",
            "capabilities": frozenset({"consume"}),
            "metadata": {"tier": "critical"},
        }
    )
    registry.register(worker)
    assert registry.get_by_name("worker") is worker
    assert registry.descriptors[0].version == "2.1.0"
    assert registry.descriptors[0].capabilities == frozenset({"consume"})


def test_descriptor_mutation_after_registration_fails_closed() -> None:
    """Core rejects identity changes instead of mixing live and frozen service metadata."""
    registry = ServiceRegistry()
    worker = service("worker")
    registry.register(worker)
    worker.descriptor = worker.descriptor.model_copy(update={"name": "renamed"})

    with pytest.raises(ValidationError, match="descriptor changed"):
        registry.ordered()
    with pytest.raises(ValidationError, match="descriptor changed"):
        registry.descriptor_for(worker)
