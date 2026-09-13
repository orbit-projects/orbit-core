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
"""Stable protocol for services implemented outside Orbit Core."""

from typing import Protocol, runtime_checkable

from orbit.health import HealthReport
from orbit.services.models import ServiceDescriptor


@runtime_checkable
class ServiceContract(Protocol):
    """The lifecycle behavior required of every managed service."""

    @property
    def descriptor(self) -> ServiceDescriptor:
        """Return the service's immutable identity and dependency metadata."""

    async def configure(self) -> None:
        """Validate and prepare service configuration."""

    async def initialize(self) -> None:
        """Allocate resources after all services are configured."""

    async def start(self) -> None:
        """Start accepting work after dependencies have started."""

    async def stop(self) -> None:
        """Stop accepting work and release resources gracefully."""

    async def health(self) -> HealthReport:
        """Return current service health."""


__all__ = ["ServiceContract"]
