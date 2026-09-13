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
"""Application rollback, concurrency, cancellation and resource ownership regressions."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from orbit import Application, ApplicationConfig, Service, ServiceDescriptor
from orbit.errors import LifecycleError
from orbit.lifecycle import LifecyclePhase as Phase
from orbit.plugins import Plugin, PluginMetadata


class Recorder(Service):
    """Instrument service hooks and optionally fail at one specific boundary."""

    def __init__(self, name, calls, fail=None, dependencies=()):
        self.descriptor = ServiceDescriptor(name=name, dependencies=dependencies)
        self.calls, self.fail = calls, fail

    async def hook(self, name):
        self.calls.append((self.descriptor.name, name))
        if self.fail == name:
            raise ValueError(name)

    async def configure(self):
        await self.hook("configure")

    async def initialize(self):
        await self.hook("initialize")

    async def start(self):
        await self.hook("start")

    async def stop(self):
        await self.hook("stop")


@pytest.mark.parametrize("stage", ["configure", "initialize", "start"])
async def test_failed_startup_cleans_partial_service_and_dependencies(stage):
    calls = []
    first = Recorder("database", calls)
    second = Recorder("api", calls, stage, (first.descriptor.id,))
    app = Application(ApplicationConfig(name="rollback"))
    app.register(second)
    app.register(first)
    with pytest.raises(ValueError, match=stage):
        await app.startup()
    assert calls[-2:] == [("api", "stop"), ("database", "stop")]
    assert app.lifecycle.phase is Phase.FAILED
    assert not app.is_ready
    before = list(calls)
    await app.stop()
    assert calls == before


async def test_shutdown_attempts_all_services_and_aggregates_errors():
    calls = []
    app = Application(ApplicationConfig(name="cleanup"))
    app.register(Recorder("one", calls, "stop"))
    app.register(Recorder("two", calls, "stop"))
    await app.startup()
    with pytest.raises(ExceptionGroup) as caught:
        await app.stop()
    assert len(caught.value.exceptions) == 2
    assert calls[-2:] == [("two", "stop"), ("one", "stop")]
    assert app.lifecycle.phase is Phase.FAILED


async def test_concurrent_startup_does_not_duplicate_hooks():
    calls = []
    app = Application(ApplicationConfig(name="concurrency"))
    app.register(Recorder("one", calls))
    results = await asyncio.gather(app.startup(), app.startup(), return_exceptions=True)
    assert sum(isinstance(result, LifecycleError) for result in results) == 1
    assert calls.count(("one", "start")) == 1
    await app.stop()


async def test_cancelled_startup_waits_for_cleanup():
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    class Block(Service):
        descriptor = ServiceDescriptor(name="block")

        async def start(self):
            entered.set()
            await asyncio.Event().wait()

        async def stop(self):
            await asyncio.sleep(0)
            cleaned.set()

    app = Application(ApplicationConfig(name="cancel"))
    app.register(Block())
    task = asyncio.create_task(app.startup())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
    assert app.lifecycle.phase is Phase.FAILED


async def test_startup_deadline_rolls_back():
    class Block(Service):
        descriptor = ServiceDescriptor(name="block")
        stopped = False

        async def start(self):
            await asyncio.Event().wait()

        async def stop(self):
            self.stopped = True

    app = Application(ApplicationConfig(name="timeout", lifecycle_timeout=0.01))
    service = Block()
    app.register(service)
    with pytest.raises(TimeoutError):
        await app.startup()
    assert service.stopped


async def test_plugin_resources_close_after_services():
    calls = []

    class Extension(Plugin):
        metadata = PluginMetadata(name="orbit-test", version="1.0.0")

        def setup(self, app):
            app.register(Recorder("service", calls))

            @asynccontextmanager
            async def resource(container):
                calls.append(("resource", "open"))
                try:
                    yield object()
                finally:
                    calls.append(("resource", "close"))

            app.container.register_resource("resource", resource)

        async def activate(self):
            calls.append(("plugin", "activate"))

        async def deactivate(self):
            calls.append(("plugin", "deactivate"))

    app = Application(ApplicationConfig(name="integration"))
    app.register_plugin(Extension())
    async with app.running():
        await app.container.aresolve("resource")
        assert app.is_ready
    assert calls[-3:] == [("service", "stop"), ("plugin", "deactivate"), ("resource", "close")]


async def test_failed_plugin_activation_is_cleaned():
    calls = []

    class Bad(Plugin):
        metadata = PluginMetadata(name="orbit-bad", version="1.0.0")

        async def activate(self):
            calls.append("activate")
            raise ValueError("activate")

        async def deactivate(self):
            calls.append("deactivate")

    app = Application(ApplicationConfig(name="plugin-failure"))
    app.register_plugin(Bad())
    with pytest.raises(ValueError):
        await app.startup()
    assert calls == ["activate", "deactivate"]


async def test_direct_registry_mutations_are_frozen():
    app = Application(ApplicationConfig(name="frozen"))
    async with app.running():
        with pytest.raises(Exception, match="frozen|closed"):
            app.services.register(Recorder("late", []))
        with pytest.raises(Exception, match="closed"):
            app.container.register_instance("late", object())
        with pytest.raises(LifecycleError):
            await app.start()


async def test_failing_observer_does_not_break_lifecycle(caplog):
    app = Application(ApplicationConfig(name="observer"))

    def broken(event):
        raise ValueError("observer")

    app.lifecycle.observe(broken)
    async with app.running():
        assert app.is_ready
    assert "Lifecycle observer failed" in caplog.text
