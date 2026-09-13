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
"""Application orchestration with transactional startup and bounded reverse cleanup.

An application is a single-use, single-event-loop owner. Public lifecycle methods serialize
on one lock. Hooks must not recursively call application lifecycle methods. Providers,
plugins and services participate in the same resource ownership hierarchy.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Mapping
from contextlib import asynccontextmanager
from types import MappingProxyType

from orbit.admin.contracts import AdminContribution
from orbit.config import ApplicationConfig, Config
from orbit.container import Container
from orbit.diagnostics.diagnostics import Diagnostics
from orbit.events import Event, EventBus
from orbit.health import HealthReport, HealthService, HealthStatus
from orbit.lifecycle import Lifecycle
from orbit.lifecycle import LifecyclePhase as Phase
from orbit.lifecycle.transition import LifecycleTransition
from orbit.plugins import PluginRegistry
from orbit.plugins.contracts import PluginContract
from orbit.routing import Router
from orbit.services import ServiceRegistry
from orbit.services.contracts import ServiceContract
from orbit.state import ApplicationState, State, StateStore
from orbit.state.models import ComponentState

_LOG = logging.getLogger(__name__)


class Application:
    """Own the complete Core graph and perform resource-safe lifecycle operations."""

    def __init__(self, config: ApplicationConfig) -> None:
        self.config = Config(config)
        self.container = Container()
        self.events = EventBus(timeout=config.lifecycle_timeout)
        self.lifecycle = Lifecycle()
        self.services = ServiceRegistry()
        self.plugins = PluginRegistry()
        self.router = Router()
        self.diagnostics = Diagnostics()
        self._admin_contributions: dict[str, AdminContribution] = {}
        self._store = StateStore(ApplicationState(application_id=config.id))
        self.state = State(self._store)
        self._entered: list[ServiceContract] = []
        self._service_phases: dict[str, Phase] = {}
        self._lock = asyncio.Lock()
        self._cleaned = False
        self.container.register_instance(Application, self)
        self.container.register_instance(ApplicationConfig, config)
        self.container.register_instance(Container, self.container)
        self.container.register_instance(EventBus, self.events)
        self.lifecycle.observe(self._reflect_lifecycle)

    @property
    def is_ready(self) -> bool:
        """Return cached readiness; health() refreshes it from current checks."""
        state = self.state.application
        return state.phase is Phase.RUNNING and state.health is HealthStatus.HEALTHY

    def register(self, service: ServiceContract) -> None:
        """Register a service during composition."""
        self.lifecycle.require(Phase.CREATED)
        self.services.register(service)
        self._service_phases[service.descriptor.name] = Phase.CREATED
        self._snapshot()

    def register_plugin(self, plugin: PluginContract) -> None:
        """Register an explicitly trusted plugin before configuration."""
        self.lifecycle.require(Phase.CREATED)
        self.plugins.register(plugin)

    def validate(self) -> None:
        """Validate component/provider graphs without acquiring resources."""
        self.services.ordered()
        self.plugins.ordered()
        self.container.validate()
        ids = {d.id for d in self.services.descriptors}
        for route in self.router.routes:
            if route.metadata.path in {"/health/live", "/health/ready", "/admin"} or (
                route.metadata.path.startswith("/admin/")
            ):
                raise ValueError("The /admin and /health endpoints are reserved for Core.")
            if route.metadata.service_id is not None and route.metadata.service_id not in ids:
                raise ValueError(f"Route {route.metadata.name} belongs to an unknown service.")

    def register_admin(self, contribution: AdminContribution) -> None:
        """Register a trusted read-only admin extension during composition."""
        self.lifecycle.require(Phase.CREATED)
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", contribution.name):
            raise ValueError("Invalid admin contribution name.")
        if contribution.name in self._admin_contributions:
            raise ValueError("Duplicate admin contribution name.")
        self._admin_contributions[contribution.name] = contribution

    @property
    def admin_contributions(self) -> Mapping[str, AdminContribution]:
        """Read the immutable administrative contribution registry."""
        return MappingProxyType(self._admin_contributions)

    async def configure(self) -> None:
        """Compose plugins, freeze registrations and configure services transactionally."""
        async with self._lock:
            self.lifecycle.require(Phase.CREATED)
            try:
                self.plugins.setup(self)
                self.validate()
                self.services.freeze()
                self.container.freeze()
                self.config.freeze()
                self.router.freeze()
                for service in self.services.ordered():
                    self._entered.append(service)
                    await self._call(service.configure())
                    self._service_phases[service.descriptor.name] = Phase.CONFIGURED
                await self.lifecycle.transition(Phase.CONFIGURED)
            except BaseException:
                await self._abort()
                raise

    async def initialize(self) -> None:
        """Activate plugins and initialize service resources; rollback on any failure."""
        async with self._lock:
            self.lifecycle.require(Phase.CONFIGURED)
            try:
                await self.plugins.activate(self.config.application.lifecycle_timeout)
                for service in self.services.ordered():
                    await self._call(service.initialize())
                    self._service_phases[service.descriptor.name] = Phase.INITIALIZED
                await self.lifecycle.transition(Phase.INITIALIZED)
            except BaseException:
                await self._abort()
                raise

    async def start(self) -> None:
        """Start services dependency-first; readiness follows a current health check."""
        async with self._lock:
            self.lifecycle.require(Phase.INITIALIZED)
            try:
                await self.lifecycle.transition(Phase.STARTING)
                for service in self.services.ordered():
                    self._service_phases[service.descriptor.name] = Phase.STARTING
                    await self._call(service.start())
                    self._service_phases[service.descriptor.name] = Phase.RUNNING
                await self.lifecycle.transition(Phase.RUNNING)
                await self.health()
            except BaseException:
                await self._abort()
                raise

    async def startup(self) -> None:
        """Run the complete startup sequence; intended for the runtime/ASGI host."""
        await self.configure()
        await self.initialize()
        await self.start()

    async def stop(self) -> None:
        """Stop all entered components, even after partial startup; idempotent after cleanup."""
        async with self._lock:
            if self._cleaned:
                return
            failures = await self._finish_cleanup()
            if failures:
                raise ExceptionGroup("Application cleanup failed", failures)

    async def health(self) -> HealthReport:
        """Refresh health from current component checks; non-running applications are unready."""
        if self.lifecycle.phase is not Phase.RUNNING:
            return HealthReport(
                status=HealthStatus.UNHEALTHY, message="Application is not running."
            )
        report = await HealthService().check(
            {s.descriptor.name: s for s in self.services.services},
            timeout=self.config.application.health_timeout,
        )
        # Shutdown may begin while checks are running.
        if self.lifecycle.phase is Phase.RUNNING:
            self._store.update(health=report.status)
            return report
        return HealthReport(status=HealthStatus.UNHEALTHY, message="Application is stopping.")

    @asynccontextmanager
    async def running(self) -> AsyncIterator[Application]:
        """Own a startup/shutdown session for workers, tests and command-line operations."""
        await self.startup()
        try:
            yield self
        finally:
            await self.stop()

    async def _call(self, hook: Awaitable[None]) -> None:
        async with asyncio.timeout(self.config.application.lifecycle_timeout):
            await hook

    async def _abort(self) -> None:
        failures = await self._finish_cleanup(failed=True)
        for failure in failures:
            _LOG.error("Rollback cleanup failed", exc_info=failure)

    async def _finish_cleanup(self, *, failed: bool = False) -> list[Exception]:
        # A separate task keeps cancellation of the caller from abandoning resources.
        task = asyncio.create_task(self._cleanup(failed=failed))
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        failures = task.result()
        if cancelled:
            raise asyncio.CancelledError
        return failures

    async def _cleanup(self, *, failed: bool) -> list[Exception]:
        if self._cleaned:
            return []
        await self.lifecycle.transition(Phase.STOPPING)
        self._store.update(health=HealthStatus.UNHEALTHY)
        failures: list[Exception] = []
        while self._entered:
            service = self._entered.pop()
            name = service.descriptor.name
            self._service_phases[name] = Phase.STOPPING
            try:
                await self._call(service.stop())
                self._service_phases[name] = Phase.STOPPED
            except (Exception, asyncio.CancelledError) as exc:
                error = (
                    exc if isinstance(exc, Exception) else RuntimeError("Cleanup hook cancelled.")
                )
                failures.append(error)
                self._service_phases[name] = Phase.FAILED
        try:
            await self.plugins.deactivate(self.config.application.lifecycle_timeout)
        except (Exception, asyncio.CancelledError) as exc:
            failures.append(
                exc if isinstance(exc, Exception) else RuntimeError("Plugin cleanup cancelled.")
            )
        try:
            await self._call(self.container.aclose())
        except (Exception, asyncio.CancelledError) as exc:
            failures.append(
                exc if isinstance(exc, Exception) else RuntimeError("Resource cleanup cancelled.")
            )
        self._cleaned = True
        await self.lifecycle.transition(Phase.FAILED if failed or failures else Phase.STOPPED)
        self.events.close()
        return failures

    async def _reflect_lifecycle(self, transition: LifecycleTransition) -> None:
        self._snapshot()
        await self.events.publish(Event(name="orbit.lifecycle", payload=transition))

    def _snapshot(self) -> None:
        self._store.update(
            phase=self.lifecycle.phase,
            service_count=len(self.services.services),
            services=tuple(
                ComponentState(name=name, phase=phase)
                for name, phase in self._service_phases.items()
            ),
            plugins=tuple(
                ComponentState(
                    name=p.metadata.name,
                    phase=Phase.RUNNING
                    if p.metadata.name in self.plugins.active_names
                    else Phase.STOPPED,
                )
                for p in self.plugins.plugins
            ),
        )


__all__ = ["Application"]
