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

import asyncio

import pytest

from orbit.application import Application
from orbit.config import ApplicationConfig
from orbit.errors import ValidationError
from orbit.health import HealthReport, HealthStatus
from orbit.lifecycle import LifecyclePhase
from orbit.plugins import Plugin, PluginMetadata
from orbit.services import Service, ServiceDescriptor
from orbit.types import new_service_id


def test_application_rejects_invalid_configuration_objects() -> None:
    """Application composition fails explicitly before reading configuration attributes."""
    with pytest.raises(TypeError, match="ApplicationConfig"):
        Application(object())  # type: ignore[arg-type]


def test_application_rejects_service_identity_mutation_after_registration() -> None:
    """Lifecycle composition uses the frozen registry identity, not mutable service metadata."""
    service = RecordingService("worker", [])
    application = Application(ApplicationConfig(name="descriptor-stability"))
    application.register(service)
    service.descriptor = service.descriptor.model_copy(update={"name": "renamed"})

    with pytest.raises(ValidationError, match="descriptor changed"):
        application.validate()


async def test_descriptor_mutation_during_startup_does_not_block_rollback() -> None:
    """Rollback retains the registered identity when a hook corrupts live metadata."""
    events: list[str] = []

    class MutatingService(RecordingService):
        async def configure(self) -> None:
            self.descriptor = self.descriptor.model_copy(update={"name": "renamed"})

    application = Application(ApplicationConfig(name="descriptor-rollback"))
    application.register(MutatingService("worker", events))

    with pytest.raises(ValidationError, match="descriptor changed"):
        await application.startup()
    assert events == ["stop:renamed"]


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


async def test_application_exposes_liveness_and_bounded_health_history() -> None:
    application = Application(ApplicationConfig(name="test-app"))
    assert application.is_live
    assert not application.is_ready

    await application.startup()
    assert application.is_live
    assert application.health_history
    assert application.health_history[-1].status.value == "healthy"
    await application.stop()
    assert not application.is_live


async def test_running_application_can_restart_leaf_service_only() -> None:
    events: list[str] = []
    service = RecordingService("worker", events)
    application = Application(ApplicationConfig(name="test-app"))
    application.register(service)
    await application.startup()
    await application.restart_service("worker")
    assert events[-2:] == ["stop:worker", "start:worker"]
    assert application.is_ready
    await application.stop()


async def test_restart_stop_failure_marks_service_failed_and_application_unhealthy() -> None:
    class FailingStop(RecordingService):
        def __init__(self, name: str, events: list[str]) -> None:
            super().__init__(name, events)
            self._failed = False

        async def stop(self) -> None:
            self._events.append(f"stop:{self.descriptor.name}")
            if not self._failed:
                self._failed = True
                raise RuntimeError("stop failed")

    events: list[str] = []
    application = Application(ApplicationConfig(name="restart-stop-failure"))
    application.register(FailingStop("worker", events))
    await application.startup()

    with pytest.raises(RuntimeError, match="stop failed"):
        await application.restart_service("worker")

    assert application.state.application.health is HealthStatus.UNHEALTHY
    assert application.state.application.services[0].phase is LifecyclePhase.FAILED
    await application.stop()


async def test_running_application_can_stop_and_start_leaf_service() -> None:
    events: list[str] = []
    service = RecordingService("worker", events)
    application = Application(ApplicationConfig(name="test-app"))
    application.register(service)
    await application.startup()
    await application.stop_service("worker")
    assert application.state.application.services[0].phase is LifecyclePhase.STOPPED
    await application.start_service("worker")
    assert events[-2:] == ["stop:worker", "start:worker"]
    await application.stop()


async def test_nested_applications_follow_parent_lifecycle() -> None:
    events: list[str] = []
    child = Application(ApplicationConfig(name="child-app"))
    child.register(RecordingService("child-service", events))
    parent = Application(ApplicationConfig(name="parent-app"))
    parent.register(RecordingService("parent-service", events))
    parent.register_child("child", child)

    await parent.startup()
    assert child.lifecycle.phase is LifecyclePhase.RUNNING
    await parent.stop()

    assert events == [
        "configure:child-service",
        "configure:parent-service",
        "initialize:child-service",
        "initialize:parent-service",
        "start:parent-service",
        "start:child-service",
        "stop:child-service",
        "stop:parent-service",
    ]
    assert child.lifecycle.phase is LifecyclePhase.STOPPED


async def test_plugin_health_is_namespaced_and_affects_readiness() -> None:
    class UnreadyPlugin(Plugin):
        metadata = PluginMetadata(name="orbit-health", version="1.0.0")

        async def health(self) -> HealthReport:
            return HealthReport(status=HealthStatus.DEGRADED, message="warming")

    application = Application(ApplicationConfig(name="plugin-health"))
    application.register_plugin(UnreadyPlugin())
    await application.startup()
    report = await application.health()
    assert report.status is HealthStatus.DEGRADED
    assert report.details["plugin:orbit-health"]["status"] == "degraded"
    assert not application.is_ready
    await application.stop()


async def test_plugin_state_and_health_names_use_frozen_registration_metadata() -> None:
    """Inspection must not let a plugin metadata mutation rename its Core state."""

    class MutablePlugin(Plugin):
        def __init__(self) -> None:
            self.metadata = PluginMetadata(name="orbit-stable", version="1.0.0")

        async def health(self) -> HealthReport:
            return HealthReport(status=HealthStatus.HEALTHY, message="ok")

    plugin = MutablePlugin()
    application = Application(ApplicationConfig(name="plugin-identity"))
    application.register_plugin(plugin)
    plugin.metadata = plugin.metadata.model_copy(update={"name": "orbit-renamed"})
    application._snapshot()  # noqa: SLF001 - verify the application-owned snapshot boundary.

    assert application.state.application.plugins[0].name == "orbit-stable"
    application.lifecycle._phase = LifecyclePhase.RUNNING  # noqa: SLF001 - test health in isolation.
    report = await application.health()
    assert "plugin:orbit-stable" in report.details
    assert "plugin:orbit-renamed" not in report.details


async def test_child_health_is_namespaced_and_affects_parent_readiness() -> None:
    class Unready(Service):
        descriptor = ServiceDescriptor(name="child-service")

        async def health(self) -> HealthReport:
            return HealthReport(status=HealthStatus.DEGRADED, message="warming")

    child = Application(ApplicationConfig(name="child-health"))
    child.register(Unready())
    parent = Application(ApplicationConfig(name="parent-health"))
    parent.register_child("child", child)
    await parent.startup()
    report = await parent.health()
    assert report.status is HealthStatus.DEGRADED
    assert report.details["child:child"]["status"] == "degraded"
    assert not parent.is_ready
    await parent.stop()


async def test_terminal_task_failure_makes_application_unready() -> None:
    application = Application(ApplicationConfig(name="task-health"))

    async def failing_task() -> None:
        raise RuntimeError("worker failed")

    application.tasks.register("worker", failing_task)
    await application.startup()
    await asyncio.sleep(0)
    report = await application.health()
    assert report.status is HealthStatus.UNHEALTHY
    assert report.details["task:worker"]["status"] == "unhealthy"
    assert not application.is_ready
    await application.stop()


async def test_configuration_watchers_follow_application_ownership() -> None:
    calls: list[str] = []

    class Watcher:
        async def start(self) -> None:
            calls.append("start")

        async def stop(self) -> None:
            calls.append("stop")

    application = Application(ApplicationConfig(name="watcher"))
    application.register_config_watcher(Watcher())
    await application.startup()
    await application.stop()
    assert calls == ["start", "stop"]


def test_nested_application_cycles_are_rejected() -> None:
    parent = Application(ApplicationConfig(name="parent-app"))
    child = Application(ApplicationConfig(name="child-app"))
    parent.register_child("child", child)
    with pytest.raises(ValueError, match="cycle"):
        child.register_child("parent", parent)


def test_deep_nested_application_cycles_do_not_use_recursive_python_stack() -> None:
    """Cycle validation remains bounded by heap traversal for deeply nested graphs."""
    root = Application(ApplicationConfig(name="deep-root"))
    current = root
    for index in range(1_050):
        child = Application(ApplicationConfig(name=f"node-{index}"))
        current.register_child(f"child-{index}", child)
        current = child

    with pytest.raises(ValueError, match="cycle"):
        current.register_child("root", root)


@pytest.mark.parametrize("name", [None, 1])
def test_nested_application_registration_rejects_non_string_names(name) -> None:
    """Nested application identity errors are explicit at the composition boundary."""
    parent = Application(ApplicationConfig(name="parent-boundary"))
    child = Application(ApplicationConfig(name="child-boundary"))
    with pytest.raises(TypeError, match="names must be strings"):
        parent.register_child(name, child)  # type: ignore[arg-type]


def test_nested_application_registration_rejects_non_application_children() -> None:
    parent = Application(ApplicationConfig(name="parent-boundary"))
    with pytest.raises(TypeError, match="Application instances"):
        parent.register_child("child", object())  # type: ignore[arg-type]


def test_configuration_watcher_registration_requires_lifecycle_hooks() -> None:
    application = Application(ApplicationConfig(name="watcher-boundary"))
    with pytest.raises(TypeError, match="start and stop"):
        application.register_config_watcher(object())  # type: ignore[arg-type]


def test_application_owned_registries_have_bounded_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Application-owned composition registries cannot retain unbounded objects."""
    monkeypatch.setattr("orbit.application.application._MAX_CORE_CAPACITY", 1)

    application = Application(ApplicationConfig(name="composition-capacity"))
    application.register_child("child", Application(ApplicationConfig(name="child-one")))
    with pytest.raises(RuntimeError, match="capacity"):
        application.register_child("second-child", Application(ApplicationConfig(name="child-two")))

    class Watcher:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    watcher = Watcher()
    application.register_config_watcher(watcher)
    with pytest.raises(RuntimeError, match="capacity"):
        application.register_config_watcher(Watcher())

    application.namespace("one")
    with pytest.raises(RuntimeError, match="capacity"):
        application.namespace("two")

    class Contribution:
        name = "first"

        async def inspect(self):
            return None

    application.register_admin(Contribution())
    with pytest.raises(RuntimeError, match="capacity"):

        class SecondContribution:
            name = "second"

            async def inspect(self):
                return None

        application.register_admin(SecondContribution())


def test_application_rejects_routes_owned_by_unknown_services() -> None:
    """Composition fails before startup when route ownership references no service."""
    application = Application(ApplicationConfig(name="route-owner"))

    @application.router.route("/missing", name="missing", service_id=new_service_id())
    async def missing(request):
        raise AssertionError("The invalid application must not dispatch this handler.")

    with pytest.raises(ValueError, match="belongs to an unknown service"):
        application.validate()


@pytest.mark.asyncio
async def test_application_context_is_bound_during_lifecycle_hooks():
    from orbit.runtime.context import current_application

    app = Application(ApplicationConfig(name="context-hooks"))
    observed = []

    class ContextService(Service):
        descriptor = ServiceDescriptor(name="context-service")

        async def configure(self):
            observed.append(current_application() is app)

    app.register(ContextService())
    async with app.running():
        assert observed == [True]
    assert current_application() is None


@pytest.mark.asyncio
async def test_service_reload_uses_hook_or_restart_fallback() -> None:
    events: list[str] = []

    class Reloadable(RecordingService):
        async def reload(self):
            events.append("reload:worker")

    app = Application(ApplicationConfig(name="reload-services"))
    app.register(Reloadable("worker", events))
    await app.startup()
    await app.reload_service("worker")
    assert events[-1] == "reload:worker"
    await app.stop()

    fallback = Application(ApplicationConfig(name="reload-fallback"))
    fallback_events: list[str] = []
    fallback.register(RecordingService("worker", fallback_events))
    await fallback.startup()
    await fallback.reload_service("worker")
    assert fallback_events[-2:] == ["stop:worker", "start:worker"]
    await fallback.stop()
