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
"""Frozen service composition and stable topological ordering."""

from __future__ import annotations

from graphlib import CycleError, TopologicalSorter

from orbit.errors import ErrorCategory, OrbitProblem, ValidationError
from orbit.services.contracts import ServiceContract
from orbit.services.models import ServiceDescriptor
from orbit.types import ServiceId


def _error(code: str, message: str) -> ValidationError:
    return ValidationError(
        OrbitProblem(code="services." + code, message=message, category=ErrorCategory.VALIDATION)
    )


class ServiceRegistry:
    """Own validated descriptors separately from user-defined mutable service objects."""

    def __init__(self) -> None:
        self._services: dict[ServiceId, ServiceContract] = {}
        self._descriptors: dict[ServiceId, ServiceDescriptor] = {}
        self._frozen = False

    def register(self, service: ServiceContract) -> None:
        """Register a unique service identity/name before composition freezes."""
        if self._frozen:
            raise _error("frozen", "Services are frozen.")
        if not isinstance(service, ServiceContract):
            raise _error("invalid-contract", "Service does not satisfy ServiceContract.")
        descriptor = ServiceDescriptor.model_validate(service.descriptor)
        if descriptor.id in self._services or any(
            d.name == descriptor.name for d in self._descriptors.values()
        ):
            raise _error("duplicate-service", f"Duplicate service: {descriptor.name}.")
        self._services[descriptor.id] = service
        self._descriptors[descriptor.id] = descriptor.model_copy(deep=True)

    @property
    def services(self) -> tuple[ServiceContract, ...]:
        """Return services in registration order."""
        return tuple(self._services.values())

    @property
    def descriptors(self) -> tuple[ServiceDescriptor, ...]:
        """Return registered metadata snapshots, independent of service mutations."""
        return tuple(self._descriptors.values())

    def freeze(self) -> None:
        """Validate and freeze composition."""
        self.ordered()
        self._frozen = True

    def ordered(self) -> tuple[ServiceContract, ...]:
        """Validate the full dependency graph and return dependency-first services."""
        for descriptor in self._descriptors.values():
            missing = set(descriptor.dependencies) - self._services.keys()
            if missing:
                raise _error("missing-dependency", f"{descriptor.name} requires {missing}.")
        graph = {key: d.dependencies for key, d in self._descriptors.items()}
        try:
            ids = tuple(TopologicalSorter(graph).static_order())
        except CycleError as exc:
            raise _error("dependency-cycle", f"Circular service dependency: {exc.args[1]}") from exc
        return tuple(self._services[key] for key in ids)

    def get(self, service_id: ServiceId) -> ServiceContract:
        """Resolve a registered service by identity; raise KeyError if absent."""
        return self._services[service_id]


__all__ = ["ServiceRegistry"]
