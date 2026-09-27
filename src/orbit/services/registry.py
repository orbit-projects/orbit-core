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

from pydantic import ValidationError as PydanticValidationError

from orbit._limits import _MAX_CORE_CAPACITY
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

    def register(self, service: ServiceContract) -> ServiceDescriptor:
        """Register a unique service and return its detached composition descriptor."""
        if self._frozen:
            raise _error("frozen", "Services are frozen.")
        if len(self._services) >= _MAX_CORE_CAPACITY:
            raise _error("capacity", "Service registry capacity reached.")
        if not isinstance(service, ServiceContract):
            raise _error("invalid-contract", "Service does not satisfy ServiceContract.")
        for hook_name in ("configure", "initialize", "start", "stop", "health"):
            if not callable(getattr(service, hook_name, None)):
                raise _error("invalid-contract", f"Service hook {hook_name} must be callable.")
        try:
            descriptor = ServiceDescriptor.model_validate(service.descriptor)
        except PydanticValidationError as exc:
            raise _error(
                "invalid-descriptor", "Service descriptor failed Core validation."
            ) from exc
        if descriptor.id in self._services or any(
            d.name == descriptor.name for d in self._descriptors.values()
        ):
            raise _error("duplicate-service", f"Duplicate service: {descriptor.name}.")
        self._services[descriptor.id] = service
        self._descriptors[descriptor.id] = descriptor.model_copy(deep=True)
        return self._descriptors[descriptor.id]

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
        for service_id, service in self._services.items():
            self._stable_descriptor(service_id, service)
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

    def get_by_name(self, name: str) -> ServiceContract:
        """Resolve a registered service by its stable descriptor name."""
        for service_id, descriptor in self._descriptors.items():
            if descriptor.name == name:
                return self._services[service_id]
        raise KeyError(name)

    def descriptor_for(self, service: ServiceContract) -> ServiceDescriptor:
        """Return the registered descriptor after verifying service identity is unchanged.

        Service implementations remain user-owned objects and may expose a mutable descriptor
        attribute. Core never follows a descriptor that changed after registration: accepting it
        would make lifecycle state and dependency ordering disagree with the frozen composition
        graph. A mutation therefore fails closed with a structured validation error.
        """
        for service_id, candidate in self._services.items():
            if candidate is service:
                return self._stable_descriptor(service_id, candidate)
        raise KeyError("Service is not registered.")

    def snapshot_for(self, service: ServiceContract) -> ServiceDescriptor:
        """Return a frozen descriptor without consulting live service metadata.

        Cleanup uses this method after a lifecycle hook has failed. It deliberately does not
        validate the mutable service object again: rollback must retain the registered identity
        even when the service violated the descriptor stability contract while failing startup.
        """
        for service_id, candidate in self._services.items():
            if candidate is service:
                return self._descriptors[service_id]
        raise KeyError("Service is not registered.")

    def _stable_descriptor(
        self, service_id: ServiceId, service: ServiceContract
    ) -> ServiceDescriptor:
        """Validate one service's live descriptor against its registration snapshot."""
        expected = self._descriptors[service_id]
        try:
            current = ServiceDescriptor.model_validate(service.descriptor)
        except Exception as exc:
            raise _error(
                "descriptor-mutated", "A registered service descriptor is no longer valid."
            ) from exc
        if current != expected:
            raise _error(
                "descriptor-mutated", "A registered service descriptor changed after registration."
            )
        return expected


__all__ = ["ServiceRegistry"]
