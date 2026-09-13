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
"""Wire-level failure and disconnect regressions using explicit ASGI frames."""

import asyncio

import pytest

from orbit import Application, ApplicationConfig
from orbit.asgi import ASGIApplication, Response
from orbit.asgi.request import HTTPError, Request


async def call(asgi, frames, *, scope=None, send=None):
    messages = []

    async def receive():
        return frames.pop(0)

    async def capture(message):
        messages.append(message)

    await asgi(
        scope or {"type": "http", "method": "POST", "path": "/", "headers": []},
        receive,
        send or capture,
    )
    return messages


async def test_disconnect_prevents_handler_execution():
    app = Application(ApplicationConfig(name="disconnect"))
    calls = []

    @app.router.route("/", method="POST", name="root")
    async def root(request):
        calls.append(1)
        return Response.text("ok")

    async with app.running():
        messages = await call(
            ASGIApplication(app),
            [
                {"type": "http.request", "body": b"partial", "more_body": True},
                {"type": "http.disconnect"},
            ],
        )
    assert not messages and not calls


async def test_chunked_request_enforces_cumulative_limit():
    app = Application(ApplicationConfig(name="chunks", max_body_bytes=4))
    async with app.running():
        messages = await call(
            ASGIApplication(app),
            [
                {"type": "http.request", "body": b"123", "more_body": True},
                {"type": "http.request", "body": b"45", "more_body": False},
            ],
        )
    assert messages[0]["status"] == 413


async def test_lifespan_failure_terminates_without_second_completion():
    from orbit import Service, ServiceDescriptor

    class Bad(Service):
        descriptor = ServiceDescriptor(name="bad")

        async def configure(self):
            raise ValueError("secret")

    app = Application(ApplicationConfig(name="lifespan"))
    app.register(Bad())
    messages = await call(
        ASGIApplication(app), [{"type": "lifespan.startup"}], scope={"type": "lifespan"}
    )
    assert [m["type"] for m in messages] == ["lifespan.startup.failed"]
    assert "secret" not in messages[0]["message"]


async def test_stream_disconnect_closes_iterator():
    app = Application(ApplicationConfig(name="stream-disconnect"))
    closed = []

    @app.router.route("/", method="POST", name="root")
    async def root(request):
        async def stream():
            try:
                yield b"one"
                yield b"two"
            finally:
                closed.append(True)

        return Response.streaming(stream())

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("peer disconnected")

    async with app.running():
        await call(ASGIApplication(app), [{"type": "http.request", "body": b""}], send=send)
    assert closed == [True]


@pytest.mark.parametrize(
    "headers",
    [
        {"x-bad": "line\r\ninjected"},
        {"bad name": "x"},
        {"transfer-encoding": "chunked"},
        {"connection": "close"},
    ],
)
def test_response_header_validation(headers):
    with pytest.raises(ValueError):
        Response(headers=headers)


@pytest.mark.parametrize("status", [0, 100, 199, 600])
def test_response_status_validation(status):
    with pytest.raises(ValueError):
        Response(status=status)


@pytest.mark.parametrize("body", [b"NaN", b"Infinity", b"\xff", b"{"])
def test_invalid_json_documents(body):
    with pytest.raises(HTTPError):
        Request("POST", "/", {"content-type": "application/json"}, body).json()


def test_strict_response_json_does_not_stringify_arbitrary_objects():
    with pytest.raises(TypeError):
        Response.json(object())
    with pytest.raises(ValueError):
        Response.json(float("nan"))


async def test_overload_rejects_instead_of_waiting_without_bound():
    app = Application(ApplicationConfig(name="overload", max_concurrent_requests=1))
    entered, release = asyncio.Event(), asyncio.Event()

    @app.router.route("/", method="POST", name="root")
    async def root(request):
        entered.set()
        await release.wait()
        return Response.text("done")

    asgi = ASGIApplication(app)
    async with app.running():
        task = asyncio.create_task(call(asgi, [{"type": "http.request", "body": b""}]))
        await entered.wait()
        rejected = await call(asgi, [{"type": "http.request", "body": b""}])
        assert rejected[0]["status"] == 503
        release.set()
        await task
