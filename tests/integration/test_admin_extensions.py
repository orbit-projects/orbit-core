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
from orbit_testing import TestClient
from pydantic import BaseModel

from orbit import Application, ApplicationConfig
from orbit.asgi import ASGIApplication
from orbit.security import Identity, Principal
from orbit.services import Service, ServiceDescriptor


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


async def test_concurrent_extension_inspections_do_not_duplicate_provider_work() -> None:
    """Concurrent admin requests fail closed instead of replacing one inspection task."""
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class View(BaseModel):
        status: str

    class Extension:
        name = "backend"

        async def inspect(self):
            nonlocal calls
            calls += 1
            started.set()
            if release.is_set():
                return View(status="recovered")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
                return View(status="recovered")

    app = Application(
        ApplicationConfig(name="admin-concurrent", admin_enabled=True, health_timeout=0.01)
    )
    extension = Extension()
    app.register_admin(extension)
    first = asyncio.create_task(
        app._inspect_admin_contribution(  # noqa: SLF001 - exercise Core ownership directly.
            "backend", extension, app.config.application.health_timeout
        )
    )
    await started.wait()
    second = asyncio.create_task(
        app._inspect_admin_contribution(  # noqa: SLF001 - exercise Core ownership directly.
            "backend", extension, app.config.application.health_timeout
        )
    )

    assert await asyncio.gather(first, second) == [None, None]
    assert calls == 1
    release.set()
    for _ in range(20):
        await asyncio.sleep(0)
        if not app._detached_admin_inspections:  # noqa: SLF001 - wait for test-owned cleanup.
            break
    assert not app._detached_admin_inspections  # noqa: SLF001 - verify one late task retired.
    recovered = await app._inspect_admin_contribution(  # noqa: SLF001 - direct Core contract test.
        "backend", extension, app.config.application.health_timeout
    )
    assert isinstance(recovered, View)


async def test_completed_extension_inspection_is_reused_before_owner_retires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed inspection cannot be replaced during its first waiter's completion gap."""
    import orbit.application.application as application_module

    original_wait = asyncio.wait
    owner_paused = asyncio.Event()
    allow_owner = asyncio.Event()
    first_wait = True
    calls = 0

    async def wait_with_completion_gap(tasks, *, timeout):
        nonlocal first_wait
        done, pending = await original_wait(tasks, timeout=timeout)
        if first_wait:
            first_wait = False
            owner_paused.set()
            await allow_owner.wait()
        return done, pending

    monkeypatch.setattr(application_module.asyncio, "wait", wait_with_completion_gap)

    class View(BaseModel):
        status: str

    class Extension:
        name = "backend"

        async def inspect(self):
            nonlocal calls
            calls += 1
            return View(status="ready")

    app = Application(ApplicationConfig(name="admin-completion-gap", admin_enabled=True))
    extension = Extension()
    first = asyncio.create_task(
        app._inspect_admin_contribution(  # noqa: SLF001 - exercise Core ownership directly.
            "backend", extension, app.config.application.health_timeout
        )
    )
    await owner_paused.wait()
    second = asyncio.create_task(
        app._inspect_admin_contribution(  # noqa: SLF001 - exercise Core ownership directly.
            "backend", extension, app.config.application.health_timeout
        )
    )
    await asyncio.sleep(0)
    allow_owner.set()

    first_view, second_view = await asyncio.gather(first, second)
    assert calls == 1
    assert isinstance(first_view, View)
    assert isinstance(second_view, View)


async def test_application_cleanup_retires_running_extension_inspection() -> None:
    """Application cleanup transfers active extension work into detached ownership."""
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    class View(BaseModel):
        status: str

    class Extension:
        name = "backend"

        async def inspect(self):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
                finished.set()
                return View(status="closed")

    app = Application(ApplicationConfig(name="admin-cleanup", admin_enabled=True))
    extension = Extension()
    inspection = asyncio.create_task(
        app._inspect_admin_contribution(  # noqa: SLF001 - exercise Core ownership directly.
            "backend", extension, app.config.application.health_timeout
        )
    )
    await started.wait()
    app._cancel_pending_admin_inspections()  # noqa: SLF001 - exercise shutdown ownership.
    assert app._detached_admin_inspections  # noqa: SLF001 - verify transferred ownership.
    release.set()
    assert isinstance(await inspection, View)
    await asyncio.wait_for(finished.wait(), timeout=0.1)
    assert not app._running_admin_inspections  # noqa: SLF001 - verify active task retirement.
    assert not app._detached_admin_inspections  # noqa: SLF001 - verify detached cleanup.


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
    records = audit.json()
    assert any(item["action"] == "service.restart" and item["success"] for item in records)
    assert {
        (item["action"], item["target"], item["success"], item["error_code"])
        for item in records
        if item["target"] in {"missing", "worker"}
    } >= {
        ("service.restart", "missing", False, "services.not-found"),
        ("task.restart", "missing", False, "tasks.not-found"),
    }
