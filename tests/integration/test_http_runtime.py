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
"""ASGI framing, middleware, authentication, readiness and resource integration."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from pydantic import BaseModel, SecretStr

from orbit import Application, ApplicationConfig, Service, ServiceDescriptor
from orbit.asgi import ASGIApplication, Response
from orbit.asgi.request import Headers, Request
from orbit.container import Scope
from orbit.health import HealthReport, HealthStatus
from orbit.security import Identity, Principal
from orbit.security.context import current_principal
from orbit.testing import TestClient


def compose(**config):
    app = Application(ApplicationConfig(name="http-tests", **config))
    return app, ASGIApplication(app)


@pytest.mark.parametrize(
    "method,status,body",
    [
        ("GET", 200, b"42"),
        ("HEAD", 200, b""),
        ("POST", 405, b""),
        ("OPTIONS", 204, b""),
    ],
)
async def test_http_method_semantics(method, status, body):
    app, asgi = compose()

    @app.router.route("/users/{id}", name="user")
    async def user(request):
        return Response.text(request.path_parameters["id"])

    async with TestClient(asgi) as client:
        response = await client.request(method, "/users/42")
        assert response.status == status
        assert response.body == body
        if method in {"POST", "OPTIONS"}:
            assert "HEAD" in response.headers["allow"]
        if method == "HEAD":
            assert response.headers["content-length"] == "2"


async def test_json_validation_and_request_error_redaction():
    app, asgi = compose()

    class Payload(BaseModel):
        number: int
        secret: SecretStr

    @app.router.route("/json", method="POST", name="json")
    async def process(request):
        return Response.json(request.validate(Payload))

    async with TestClient(asgi) as client:
        good = await client.request(
            "POST",
            "/json",
            body=b'{"number":2,"secret":"private"}',
            headers={"content-type": "application/json"},
        )
        assert good.status == 200
        assert b"private" not in good.body
        bad = await client.request(
            "POST",
            "/json",
            body=b'{"number":"private"}',
            headers={"content-type": "application/json"},
        )
        assert bad.status == 422 and b"private" not in bad.body
        malformed = await client.request(
            "POST", "/json", body=b"{", headers={"content-type": "application/json"}
        )
        assert malformed.status == 400
        assert (await client.request("POST", "/json")).status == 415


@pytest.mark.parametrize(
    "body,headers,status",
    [
        (b"12345", {}, 413),
        (b"1", {"content-length": "9"}, 413),
        (b"1", {"content-length": "2"}, 400),
        (b"1", {"content-length": "-1"}, 400),
    ],
)
async def test_body_limits_and_length_validation(body, headers, status):
    app, asgi = compose(max_body_bytes=4)
    async with TestClient(asgi) as client:
        response = await client.request("POST", "/missing", body=body, headers=headers)
        assert response.status == status


async def test_middleware_order_and_scope_resource_cleanup():
    app, asgi = compose()
    calls = []

    @asynccontextmanager
    async def resource(container):
        calls.append("open")
        try:
            yield object()
        finally:
            calls.append("close")

    app.container.register_resource("request", resource, scope=Scope.SCOPED)

    async def outer(request, next_handler):
        calls.append("before")
        response = await next_handler(request)
        calls.append("after")
        return response

    asgi.add_middleware(outer)

    @app.router.route("/scope", name="scope")
    async def handler(request):
        one = await request.container.aresolve("request")
        assert one is await request.container.aresolve("request")
        return Response.text("ok")

    async with TestClient(asgi) as client:
        assert (await client.request("GET", "/scope")).status == 200
    assert calls == ["before", "open", "after", "close"]


async def test_streaming_preserves_resource_scope_and_repeated_headers():
    app, asgi = compose()
    calls = []

    @asynccontextmanager
    async def resource(container):
        try:
            yield "resource"
        finally:
            calls.append("closed")

    app.container.register_resource("stream", resource, scope=Scope.SCOPED)

    @app.router.route("/stream", name="stream")
    async def handler(request):
        async def chunks():
            try:
                assert await request.container.aresolve("stream") == "resource"
                yield b"one"
                assert not calls
                yield b"two"
            finally:
                calls.append("stream-closed")

        return Response(stream=chunks(), headers=[("set-cookie", "a=1"), ("set-cookie", "b=2")])

    async with TestClient(asgi) as client:
        result = await client.request("GET", "/stream")
        assert result.body == b"onetwo"
        assert result.headers.getall("set-cookie") == ("a=1", "b=2")
        assert "content-length" not in result.headers
    assert calls == ["stream-closed", "closed"]


async def test_current_readiness_reflects_changed_health():
    class Switch(Service):
        descriptor = ServiceDescriptor(name="switch")
        status = HealthStatus.HEALTHY

        async def health(self):
            return HealthReport(status=self.status)

    app, asgi = compose()
    service = Switch()
    app.register(service)
    async with TestClient(asgi) as client:
        assert (await client.request("GET", "/health/ready")).status == 200
        service.status = HealthStatus.UNHEALTHY
        assert (await client.request("GET", "/health/ready")).status == 503
        assert (await client.request("GET", "/health/live")).status == 200


class Auth:
    """Deterministic test authentication, never a production credential implementation."""

    async def authenticate(self, request):
        token = request.headers.get("authorization", "")
        roles = {
            "Bearer reader": {"orbit.admin.read"},
            "Bearer writer": {"orbit.admin.read", "orbit.admin.write"},
        }.get(token)
        return (
            Principal(identity=Identity(subject="test", provider="test"), roles=roles)
            if roles
            else None
        )


@pytest.mark.parametrize(
    "token,path,status",
    [
        ("", "/admin", 401),
        ("Bearer reader", "/admin", 200),
        ("Bearer reader", "/admin/config", 200),
        ("Bearer reader", "/admin/missing", 404),
        ("Bearer reader", "/admin/services", 200),
        ("Bearer reader", "/admin/plugins", 200),
        ("Bearer reader", "/admin/routes", 200),
        ("Bearer reader", "/admin/dependencies", 200),
        ("Bearer reader", "/admin/events", 200),
        ("Bearer reader", "/admin/state", 200),
    ],
)
async def test_admin_requires_auth_and_serves_actual_core_views(token, path, status):
    app, _ = compose(admin_enabled=True)
    asgi = ASGIApplication(app, authenticator=Auth())
    async with TestClient(asgi) as client:
        result = await client.request("GET", path, headers={"authorization": token})
        assert result.status == status
        if status == 200:
            assert result.headers["cache-control"] == "no-store"
    assert current_principal() is None


@pytest.mark.parametrize(
    "token,origin,status",
    [
        ("Bearer reader", "", 403),
        ("Bearer writer", "https://evil.example", 403),
        ("Bearer writer", "", 200),
    ],
)
async def test_admin_mutation_requires_write_role_and_explicit_authority(token, origin, status):
    app, _ = compose(admin_enabled=True)
    asgi = ASGIApplication(app, authenticator=Auth())
    headers = {"authorization": token}
    if origin:
        headers["origin"] = origin
    async with TestClient(asgi) as client:
        response = await client.request("POST", "/admin/health/refresh", headers=headers)
        assert response.status == status


async def test_admin_disabled_and_protected_user_route():
    app, asgi = compose()

    @app.router.route("/private", name="private", roles=frozenset({"private"}))
    async def private(request):
        return Response.text("private")

    async with TestClient(asgi) as client:
        assert (await client.request("GET", "/admin")).status == 404
        assert (await client.request("GET", "/private")).status == 401


async def test_internal_error_never_leaks_exception_details():
    app, asgi = compose()

    @app.router.route("/fail", name="fail")
    async def fail(request):
        raise ValueError("private-database-password")

    async with TestClient(asgi) as client:
        response = await client.request("GET", "/fail")
        assert response.status == 500
        assert b"private-database-password" not in response.body
        assert response.headers["x-request-id"] == response.json()["request_id"]


async def test_request_deadline_cleans_scope():
    app, asgi = compose(request_timeout=0.01)
    calls = []

    @asynccontextmanager
    async def resource(container):
        try:
            yield 1
        finally:
            calls.append("closed")

    app.container.register_resource("r", resource, scope=Scope.SCOPED)

    @app.router.route("/slow", name="slow")
    async def slow(request):
        await request.container.aresolve("r")
        await asyncio.Event().wait()

    async with TestClient(asgi) as client:
        assert (await client.request("GET", "/slow")).status == 504
        assert calls == ["closed"]


async def test_duplicate_headers_and_query_parameters():
    headers = Headers([("X-Test", "one"), ("x-test", "two")])
    assert headers.getall("X-Test") == ("one", "two")
    assert headers["X-TEST"] == "one"
    assert len(headers) == 1 and list(headers) == ["x-test"]
    request = Request("GET", "/", headers, query_string=b"x=1&x=2&blank=")
    assert request.query_parameters == {"x": ["1", "2"], "blank": [""]}
    with pytest.raises(KeyError):
        _ = headers["absent"]
