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
"""Real ASGI lifespan, plugin composition and operational failure behavior."""

import asyncio
import json
import logging

from orbit import Application, ApplicationConfig
from orbit.asgi import ASGIApplication, Response
from orbit.diagnostics import JSONFormatter
from orbit.plugins import Plugin, PluginMetadata
from orbit.runtime.context import current_request_id
from orbit.testing import TestClient


async def test_plugin_routes_are_composed_before_runtime_freezes():
    class Extension(Plugin):
        metadata = PluginMetadata(name="orbit-routes", version="1.0.0")

        def setup(self, app):
            @app.router.route("/plugin", method="GET", name="plugin")
            async def route(request):
                return Response.text("plugin")

    app = Application(ApplicationConfig(name="plugin-runtime"))
    app.register_plugin(Extension())
    async with TestClient(ASGIApplication(app)) as client:
        assert (await client.request("GET", "/plugin")).body == b"plugin"


async def test_overload_has_correlation_and_is_counted():
    app = Application(ApplicationConfig(name="overload", max_concurrent_requests=1))
    entered, release = asyncio.Event(), asyncio.Event()

    @app.router.route("/", method="GET", name="root")
    async def route(request):
        entered.set()
        await release.wait()
        return Response.text("ok")

    async with TestClient(ASGIApplication(app)) as client:
        first = asyncio.create_task(client.request("GET", "/"))
        await entered.wait()
        overloaded = await client.request("GET", "/")
        assert overloaded.status == 503
        assert overloaded.headers["x-request-id"]
        release.set()
        await first
        snapshot = app.diagnostics.collect(app)
        assert snapshot.request_count == 2
        assert snapshot.error_count == 1
        assert snapshot.status_counts == {503: 1, 200: 1}


async def test_request_context_is_isolated_and_logged():
    app = Application(ApplicationConfig(name="correlation"))

    @app.router.route("/", method="GET", name="root")
    async def route(request):
        await asyncio.sleep(0)
        record = logging.LogRecord("worker", logging.INFO, "", 0, "handled", (), None)
        data = json.loads(JSONFormatter().format(record))
        assert data["request_id"] == request.request_id == current_request_id()
        assert data["application"] == "correlation"
        return Response.json(data)

    async with TestClient(ASGIApplication(app)) as client:
        first, second = await asyncio.gather(client.request("GET", "/"), client.request("GET", "/"))
        assert first.json()["request_id"] != second.json()["request_id"]
    assert current_request_id() is None


async def test_error_response_disconnect_does_not_escape_runtime():
    app = Application(ApplicationConfig(name="disconnect-error"))

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        raise OSError("disconnected")

    async with app.running():
        await ASGIApplication(app)(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [(b"content-length", b"invalid")],
            },
            receive,
            send,
        )
    assert app.diagnostics.collect(app).recent_requests[-1].outcome == "disconnected"


async def test_oversized_content_length_is_client_error():
    app = Application(ApplicationConfig(name="length"))
    async with TestClient(ASGIApplication(app)) as client:
        response = await client.request("GET", "/", headers={"content-length": "9" * 5000})
        assert response.status == 413
