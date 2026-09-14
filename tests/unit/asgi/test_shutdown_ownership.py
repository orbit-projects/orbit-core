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
"""Runtime shutdown owns outstanding requests even if the host task is cancelled."""

import asyncio

import pytest

from orbit import Application, ApplicationConfig
from orbit.asgi import ASGIApplication, Response
from orbit.asgi.lifespan import handle_lifespan
from orbit.lifecycle import LifecyclePhase


async def test_lifespan_startup_acknowledgement_failure_still_closes_application():
    app = Application(ApplicationConfig(name="acknowledgement"))

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        raise OSError("host disconnected")

    with pytest.raises(OSError):
        await handle_lifespan(app, receive, send)
    assert app.lifecycle.phase is LifecyclePhase.STOPPED


async def test_shutdown_cancellation_waits_for_request_resource_cleanup():
    app = Application(ApplicationConfig(name="drain", lifecycle_timeout=0.01))
    runtime = ASGIApplication(app)
    entered = asyncio.Event()
    cleaned = []

    @app.router.route("/", method="GET", name="root")
    async def route(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.append(True)
        return Response.text("unreachable")

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        return None

    await app.startup()
    request = asyncio.create_task(
        runtime({"type": "http", "method": "GET", "path": "/"}, receive, send)
    )
    await entered.wait()
    shutdown = asyncio.create_task(runtime.shutdown())
    await asyncio.sleep(0)
    shutdown.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    await asyncio.gather(request, return_exceptions=True)
    assert cleaned == [True]
    assert app.lifecycle.phase is LifecyclePhase.STOPPED
    assert app.diagnostics.collect(app).cancelled_count == 1
    await runtime.shutdown()
