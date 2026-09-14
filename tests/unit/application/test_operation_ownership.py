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
"""Lifecycle transaction ownership and shared health execution."""

import asyncio

import pytest

from orbit import Application, ApplicationConfig, Service, ServiceDescriptor
from orbit.errors import LifecycleError
from orbit.health import HealthReport, HealthStatus
from orbit.lifecycle import LifecyclePhase


async def test_stop_waits_for_entire_startup_transaction():
    configured, release = asyncio.Event(), asyncio.Event()
    calls = []

    class Worker(Service):
        descriptor = ServiceDescriptor(name="worker")

        async def configure(self):
            configured.set()
            await release.wait()

        async def initialize(self):
            calls.append("initialize")

        async def start(self):
            calls.append("start")

        async def stop(self):
            calls.append("stop")

    app = Application(ApplicationConfig(name="atomic"))
    app.register(Worker())
    starting = asyncio.create_task(app.startup())
    await configured.wait()
    stopping = asyncio.create_task(app.stop())
    release.set()
    await asyncio.gather(starting, stopping)
    assert calls == ["initialize", "start", "stop"]
    assert app.lifecycle.phase is LifecyclePhase.STOPPED


async def test_reentrant_lifecycle_hook_fails_without_waiting_for_timeout():
    app = Application(ApplicationConfig(name="reentrant"))

    class Worker(Service):
        descriptor = ServiceDescriptor(name="worker")

        async def start(self):
            await app.stop()

    app.register(Worker())
    async with asyncio.timeout(1):
        with pytest.raises(LifecycleError, match="cannot initiate"):
            await app.startup()
    assert app.lifecycle.phase is LifecyclePhase.FAILED


async def test_health_is_shared_and_a_cancelled_waiter_does_not_cancel_check():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0
    block = False

    class Worker(Service):
        descriptor = ServiceDescriptor(name="worker")

        async def health(self):
            nonlocal calls
            calls += 1
            if block:
                entered.set()
                await release.wait()
            return HealthReport(status=HealthStatus.HEALTHY)

    app = Application(ApplicationConfig(name="health-sharing"))
    app.register(Worker())
    async with app.running():
        assert app.state.application.services[0].health is HealthStatus.HEALTHY
        block = True
        one = asyncio.create_task(app.health())
        await entered.wait()
        two = asyncio.create_task(app.health())
        await asyncio.sleep(0)
        one.cancel()
        with pytest.raises(asyncio.CancelledError):
            await one
        release.set()
        assert (await two).status is HealthStatus.HEALTHY
        assert calls == 2
