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
"""Deliberate composition helper for creating Orbit applications."""

from orbit.application.application import Application
from orbit.config import ApplicationConfig
from orbit.services.contracts import ServiceContract


class ApplicationBuilder:
    """Collect services before constructing an application with validated Core config."""

    def __init__(self, config: ApplicationConfig) -> None:
        """Start composition from ``config``."""
        self._config = config
        self._services: list[ServiceContract] = []

    def service(self, service: ServiceContract) -> "ApplicationBuilder":
        """Add a service and return the builder for fluent composition."""
        self._services.append(service)
        return self

    def build(self) -> Application:
        """Construct an application and register all collected services."""
        application = Application(self._config)
        for service in self._services:
            application.register(service)
        return application


__all__ = ["ApplicationBuilder"]
