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
"""Admin extensions cannot disable Core inspection or expose failure details."""

import asyncio

import pytest

from orbit import Application, ApplicationConfig
from orbit.asgi import ASGIApplication
from orbit.security import Identity, Principal
from orbit.testing import TestClient


@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_extension_failure_isolated_and_not_cached(failure):
    class Auth:
        async def authenticate(self, request):
            return Principal(
                identity=Identity(subject="operator", provider="test"),
                roles=frozenset({"orbit.admin.read"}),
            )

    class Extension:
        name = "backend"

        async def inspect(self):
            if failure == "timeout":
                await asyncio.Event().wait()
            raise ValueError("secret backend credentials")

    app = Application(
        ApplicationConfig(name="admin-extensions", admin_enabled=True, health_timeout=0.01)
    )
    app.register_admin(Extension())
    async with TestClient(ASGIApplication(app, authenticator=Auth())) as client:
        page = await client.request("GET", "/admin")
        assert page.status == 200
        assert b"unavailable" in page.body
        assert b"secret" not in page.body
        response = await client.request("GET", "/admin/extensions/backend")
        assert response.json()["code"] == "admin.extension-failed"
        assert response.headers["cache-control"] == "no-store"
        assert (await client.request("GET", "/admin/lifecycle")).status == 200
        assert (await client.request("GET", "/admin/health")).status == 200


async def test_unauthenticated_admin_errors_are_not_cacheable():
    app = Application(ApplicationConfig(name="protected", admin_enabled=True))
    async with TestClient(ASGIApplication(app)) as client:
        response = await client.request("GET", "/admin/config")
        assert response.status == 401
        assert response.headers["cache-control"] == "no-store"
