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
"""Convenient base class for services with safe lifecycle defaults."""

from orbit.health import HealthReport, HealthStatus
from orbit.services.models import ServiceDescriptor


class Service:
    """Base implementation for a service whose hooks are selectively overridden."""

    descriptor: ServiceDescriptor

    async def configure(self) -> None:
        """Prepare configuration; subclasses override when they have configuration work."""
        return None

    async def initialize(self) -> None:
        """Allocate resources; subclasses override when they own resources."""
        return None

    async def start(self) -> None:
        """Start work; subclasses override when they accept traffic or messages."""
        return None

    async def stop(self) -> None:
        """Release resources; subclasses override for graceful shutdown."""
        return None

    async def health(self) -> HealthReport:
        """Report healthy by default once a service is registered."""
        return HealthReport(status=HealthStatus.HEALTHY)


__all__ = ["Service"]
