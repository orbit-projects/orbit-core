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
"""The testing harness fails on protocol violations and reproduces decoded ASGI paths."""

import asyncio

import pytest

from orbit import Application, ApplicationConfig
from orbit.asgi import ASGIApplication, Response
from orbit.testing import TestClient


async def test_client_requires_context_and_rejects_reuse():
    client = TestClient(ASGIApplication(Application(ApplicationConfig(name="client"))))
    with pytest.raises(RuntimeError, match="context"):
        await client.request("GET", "/")
    async with client:
        pass
    with pytest.raises(RuntimeError, match="single-use"):
        await client.__aenter__()


async def test_lifespan_crash_is_observed_immediately():
    class Crashed:
        async def __call__(self, scope, receive, send):
            raise ValueError("lifespan crash")

    async with asyncio.timeout(1):
        with pytest.raises(ValueError, match="lifespan crash"):
            async with TestClient(Crashed()):
                pytest.fail("startup must fail")


@pytest.mark.parametrize(
    "frames",
    [
        [{"type": "http.response.body", "body": b""}],
        [{"type": "http.response.start", "status": 200, "headers": []}],
        [
            {"type": "http.response.start", "status": 200, "headers": []},
            {"type": "http.response.start", "status": 200, "headers": []},
        ],
        [
            {"type": "http.response.start", "status": 200, "headers": []},
            {"type": "http.response.body", "body": b""},
            {"type": "http.response.body", "body": b""},
        ],
    ],
)
async def test_invalid_response_framing_fails_the_test(frames):
    class Broken:
        async def __call__(self, scope, receive, send):
            if scope["type"] == "lifespan":
                await receive()
                await send({"type": "lifespan.startup.complete"})
                await receive()
                await send({"type": "lifespan.shutdown.complete"})
            else:
                for frame in frames:
                    await send(frame)

    async with TestClient(Broken()) as client:
        with pytest.raises(AssertionError):
            await client.request("GET", "/")


async def test_encoded_path_and_repeated_headers_reach_handler():
    app = Application(ApplicationConfig(name="wire"))

    @app.router.route("/café", method="GET", name="cafe")
    async def route(request):
        return Response.json({"values": request.headers.getall("x-value")})

    async with TestClient(ASGIApplication(app)) as client:
        response = await client.request(
            "get", "/caf%C3%A9", headers=[("X-Value", "one"), ("x-value", "two")]
        )
        assert response.status == 200
        assert response.json() == {"values": ["one", "two"]}


@pytest.mark.parametrize("timeout", [0, float("nan"), float("inf")])
def test_invalid_lifespan_timeout(timeout):
    with pytest.raises(ValueError):
        TestClient(
            ASGIApplication(Application(ApplicationConfig(name="timeout"))),
            lifespan_timeout=timeout,
        )
