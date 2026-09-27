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
from pydantic import BaseModel

from orbit import Application, ApplicationConfig
from orbit.asgi import ASGIApplication
from orbit.security import Identity, Principal
from orbit.services import Service, ServiceDescriptor
from orbit.testing import TestClient


@pytest.mark.parametrize(
    "contribution",
    [object(), type("MissingInspect", (), {"name": "bad"})()],
)
def test_admin_registration_rejects_invalid_contracts(contribution) -> None:
    """Invalid extensions fail during composition instead of leaking attribute errors."""
    app = Application(ApplicationConfig(name="admin-contract"))
    with pytest.raises(TypeError, match="Admin contribution"):
        app.register_admin(contribution)  # type: ignore[arg-type]


async def test_admin_inspection_rejects_non_awaitable_results() -> None:
    """A synchronous inspect result is isolated instead of entering task construction."""

    class Auth:
        async def authenticate(self, request):
            return Principal(
                identity=Identity(subject="operator", provider="test"),
                roles=frozenset({"orbit.admin.read"}),
            )

    class Extension:
        name = "sync"

        def inspect(self):
            return BaseModel()

    app = Application(ApplicationConfig(name="admin-sync-contract", admin_enabled=True))
    app.register_admin(Extension())
    async with TestClient(ASGIApplication(app, authenticator=Auth())) as client:
        response = await client.request("GET", "/admin/extensions/sync")
    assert response.json() == {"status": "unavailable", "code": "admin.extension-failed"}


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


async def test_cancellation_resistant_extension_is_detached_and_retried() -> None:
    """One stubborn extension cannot hang admin requests or create concurrent inspections."""
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    class View(BaseModel):
        status: str

    class Auth:
        async def authenticate(self, request):
            return Principal(
                identity=Identity(subject="operator", provider="test"),
                roles=frozenset({"orbit.admin.read"}),
            )

    class Extension:
        name = "backend"

        async def inspect(self):
            started.set()
            if release.is_set():
                return View(status="recovered")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
                finished.set()
                return View(status="recovered")

    app = Application(
        ApplicationConfig(name="admin-stubborn", admin_enabled=True, health_timeout=0.01)
    )
    app.register_admin(Extension())
    async with TestClient(ASGIApplication(app, authenticator=Auth())) as client:
        first = await asyncio.wait_for(
            client.request("GET", "/admin/extensions/backend"), timeout=0.1
        )
        assert first.json()["code"] == "admin.extension-failed"
        assert started.is_set()

        repeated = await client.request("GET", "/admin/extensions/backend")
        assert repeated.json()["code"] == "admin.extension-failed"
        assert not finished.is_set()

        release.set()
        await asyncio.wait_for(finished.wait(), timeout=0.1)
        recovered = await client.request("GET", "/admin/extensions/backend")
        assert recovered.json() == {"status": "recovered"}


async def test_unauthenticated_admin_errors_are_not_cacheable():
    app = Application(ApplicationConfig(name="protected", admin_enabled=True))
    async with TestClient(ASGIApplication(app)) as client:
        response = await client.request("GET", "/admin/config")
        assert response.status == 401
        assert response.headers["cache-control"] == "no-store"


async def test_admin_mutations_require_explicit_authorization_and_audit_success():
    class Auth:
        async def authenticate(self, request):
            return Principal(
                identity=Identity(subject="operator", provider="test"),
                roles=frozenset({"orbit.admin.read", "orbit.admin.write"}),
            )

    class Managed(Service):
        descriptor = ServiceDescriptor(name="managed")

    app = Application(ApplicationConfig(name="admin-mutations", admin_enabled=True))
    app.register(Managed())

    async def task() -> None:
        await asyncio.Event().wait()

    app.tasks.register("worker", task)
    async with TestClient(ASGIApplication(app, authenticator=Auth())) as client:
        csrf = await client.request(
            "POST", "/admin/services/managed/restart", headers={"origin": "https://evil.example"}
        )
        assert csrf.status == 403
        duplicate_authorization = await client.request(
            "POST",
            "/admin/services/managed/restart",
            headers=[
                ("authorization", "Bearer x"),
                ("authorization", "Bearer y"),
            ],
        )
        assert duplicate_authorization.status == 403
        invalid = await client.request(
            "POST", "/admin/services/managed/unknown", headers={"authorization": "Bearer x"}
        )
        assert invalid.status == 400
        invalid_name = await client.request(
            "POST", "/admin/services/Managed/restart", headers={"authorization": "Bearer x"}
        )
        assert invalid_name.status == 400
        restarted = await client.request(
            "POST", "/admin/services/managed/restart", headers={"authorization": "Bearer x"}
        )
        assert restarted.status == 200
        assert restarted.json()["status"] == "restarted"
        for action in ("stop", "start", "reload"):
            result = await client.request(
                "POST",
                f"/admin/services/managed/{action}",
                headers={"authorization": "Bearer x"},
            )
            assert result.status == 200
        task_result = await client.request(
            "POST", "/admin/tasks/worker/restart", headers={"authorization": "Bearer x"}
        )
        assert task_result.status == 200
        invalid_task = await client.request(
            "POST", "/admin/tasks/worker/stop", headers={"authorization": "Bearer x"}
        )
        assert invalid_task.status == 400
        invalid_task_name = await client.request(
            "POST", "/admin/tasks/Worker/restart", headers={"authorization": "Bearer x"}
        )
        assert invalid_task_name.status == 400
        missing_task = await client.request(
            "POST", "/admin/tasks/missing/restart", headers={"authorization": "Bearer x"}
        )
        assert missing_task.status == 404
        missing_service = await client.request(
            "POST", "/admin/services/missing/restart", headers={"authorization": "Bearer x"}
        )
        assert missing_service.status == 404
        unknown = await client.request("GET", "/admin/unknown")
        assert unknown.status == 404
        method = await client.request("PUT", "/admin")
        assert method.status == 405
        refresh = await client.request(
            "POST", "/admin/health/refresh", headers={"authorization": "Bearer x"}
        )
        assert refresh.status == 200
        audit = await client.request("GET", "/admin/audit")
    assert any(item["action"] == "service.restart" for item in audit.json())
