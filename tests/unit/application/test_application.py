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
"""Application orchestration behavior tests."""

from orbit.application import Application
from orbit.config import ApplicationConfig
from orbit.lifecycle import LifecyclePhase
from orbit.services import Service, ServiceDescriptor


class RecordingService(Service):
    """Service double that records all lifecycle calls for ordering assertions."""

    def __init__(self, name: str, events: list[str], dependencies: tuple = ()) -> None:
        """Create a service whose hooks append to ``events``."""
        self.descriptor = ServiceDescriptor(name=name, dependencies=dependencies)
        self._events = events

    async def configure(self) -> None:
        """Record configuration."""
        self._events.append(f"configure:{self.descriptor.name}")

    async def initialize(self) -> None:
        """Record initialization."""
        self._events.append(f"initialize:{self.descriptor.name}")

    async def start(self) -> None:
        """Record startup."""
        self._events.append(f"start:{self.descriptor.name}")

    async def stop(self) -> None:
        """Record shutdown."""
        self._events.append(f"stop:{self.descriptor.name}")


async def test_application_orders_dependencies_and_stops_in_reverse() -> None:
    """Services start after dependencies and stop before their dependencies."""
    events: list[str] = []
    database = RecordingService("database", events)
    api = RecordingService("api", events, (database.descriptor.id,))
    application = Application(ApplicationConfig(name="test-app"))
    application.register(api)
    application.register(database)

    await application.configure()
    await application.initialize()
    await application.start()
    await application.stop()

    assert events == [
        "configure:database",
        "configure:api",
        "initialize:database",
        "initialize:api",
        "start:database",
        "start:api",
        "stop:api",
        "stop:database",
    ]
    assert application.lifecycle.phase is LifecyclePhase.STOPPED
