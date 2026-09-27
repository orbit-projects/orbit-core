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
"""HTTP rate-limiting middleware integration coverage."""

import pytest

from orbit import Application, ApplicationConfig
from orbit.asgi import ASGIApplication, RateLimitMiddleware, Response
from orbit.testing import TestClient


@pytest.mark.asyncio
async def test_rate_limit_middleware_rejects_and_emits_headers() -> None:
    app = Application(ApplicationConfig(name="rate-limit"))
    asgi = ASGIApplication(app)
    asgi.add_middleware(RateLimitMiddleware(1, 60, key=lambda request: "test-client"))

    @app.router.route("/", name="root")
    async def root(request):
        return Response.text("ok")

    async with TestClient(asgi) as client:
        first = await client.request("GET", "/")
        second = await client.request("GET", "/")
    assert first.status == 200
    assert first.headers["x-ratelimit-limit"] == "1"
    assert first.headers["x-ratelimit-remaining"] == "0"
    assert second.status == 429
    assert second.headers["retry-after"] == "60"
    assert second.headers["x-ratelimit-remaining"] == "0"
