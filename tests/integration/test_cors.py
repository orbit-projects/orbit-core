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
"""CORS policy and preflight integration tests."""

import pytest

from orbit import Application, ApplicationConfig
from orbit.asgi import ASGIApplication, CORSMiddleware, Response
from orbit.testing import TestClient


@pytest.mark.asyncio
async def test_cors_allows_explicit_origin_and_preflight() -> None:
    app = Application(ApplicationConfig(name="cors"))
    asgi = ASGIApplication(app)
    asgi.add_middleware(
        CORSMiddleware(
            allow_origins=("https://client.example",),
            allow_methods=("GET", "POST", "OPTIONS"),
            allow_headers=("content-type",),
            expose_headers=("x-request-id",),
            allow_credentials=True,
        )
    )

    @app.router.route("/data", name="data")
    async def data(request):
        return Response.text("ok")

    async with TestClient(asgi) as client:
        response = await client.request(
            "GET", "/data", headers={"origin": "https://client.example"}
        )
        preflight = await client.request(
            "OPTIONS",
            "/data",
            headers={
                "origin": "https://client.example",
                "access-control-request-method": "POST",
                "access-control-request-headers": "content-type",
            },
        )
    assert response.headers["access-control-allow-origin"] == "https://client.example"
    assert response.headers["access-control-allow-credentials"] == "true"
    assert preflight.status == 204
    assert preflight.headers["access-control-allow-methods"] == "GET, POST, OPTIONS"


@pytest.mark.asyncio
async def test_cors_rejects_disallowed_origin_and_wildcard_credentials() -> None:
    with pytest.raises(ValueError, match="Wildcard"):
        CORSMiddleware(allow_origins=("*",), allow_credentials=True)
    app = Application(ApplicationConfig(name="cors-reject"))
    asgi = ASGIApplication(app)
    asgi.add_middleware(CORSMiddleware(allow_origins=("https://trusted.example",)))
    async with TestClient(asgi) as client:
        response = await client.request(
            "GET", "/missing", headers={"origin": "https://evil.example"}
        )
    assert response.status == 404
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_cors_replaces_conflicting_headers_and_preserves_vary() -> None:
    app = Application(ApplicationConfig(name="cors-headers"))
    asgi = ASGIApplication(app)
    asgi.add_middleware(CORSMiddleware(allow_origins=("https://trusted.example",)))

    @app.router.route("/data", name="data")
    async def data(request):
        return Response(
            headers={
                "access-control-allow-origin": "https://attacker.example",
                "vary": "Accept-Encoding",
            }
        )

    async with TestClient(asgi) as client:
        response = await client.request(
            "GET", "/data", headers={"origin": "https://trusted.example"}
        )
    assert response.headers["access-control-allow-origin"] == "https://trusted.example"
    assert response.headers["vary"] == "Accept-Encoding, Origin"


@pytest.mark.asyncio
async def test_cors_deduplicates_vary_tokens_case_insensitively() -> None:
    """Vary field names use HTTP's case-insensitive token semantics."""
    app = Application(ApplicationConfig(name="cors-vary-case"))
    asgi = ASGIApplication(app)
    asgi.add_middleware(CORSMiddleware(allow_origins=("https://trusted.example",)))

    @app.router.route("/data", name="data")
    async def data(request):
        return Response(headers={"vary": "origin, Accept-Encoding, Origin"})

    async with TestClient(asgi) as client:
        response = await client.request(
            "GET", "/data", headers={"origin": "https://trusted.example"}
        )
    assert response.headers["vary"] == "origin, Accept-Encoding"


@pytest.mark.asyncio
async def test_cors_rejects_ambiguous_duplicate_browser_policy_headers() -> None:
    app = Application(ApplicationConfig(name="cors-duplicates"))
    asgi = ASGIApplication(app)
    asgi.add_middleware(CORSMiddleware(allow_origins=("https://trusted.example",)))

    @app.router.route("/data", name="data")
    async def data(request):
        return Response.text("ok")

    async with TestClient(asgi) as client:
        response = await client.request(
            "GET",
            "/data",
            headers=[
                ("origin", "https://trusted.example"),
                ("origin", "https://attacker.example"),
            ],
        )
        preflight = await client.request(
            "OPTIONS",
            "/data",
            headers=[
                ("origin", "https://trusted.example"),
                ("access-control-request-method", "GET"),
                ("access-control-request-method", "POST"),
            ],
        )
    assert response.status == 200
    assert "access-control-allow-origin" not in response.headers
    assert preflight.status == 403


@pytest.mark.parametrize(
    "origin",
    (
        "client.example",
        "ftp://client.example",
        "https://client.example/path",
        "https://user:pass@client.example",
        "https://client.example?token=secret",
        "https://client.example:",
        "https://client.example?",
        "https://client.example#",
        "https://client.example\x00",
        "https://[invalid",
        "https://[fe80::1%25eth0]",
    ),
)
def test_cors_rejects_ambiguous_origin_policy(origin: str) -> None:
    with pytest.raises(ValueError, match="valid HTTP or HTTPS"):
        CORSMiddleware(allow_origins=(origin,))


def test_cors_policy_bounds_methods_and_cache_duration() -> None:
    policy = CORSMiddleware(allow_origins=("https://client.example",))
    with pytest.raises(AttributeError):
        policy.allow_origins = ()  # type: ignore[misc]
    with pytest.raises(AttributeError):
        policy.allow_credentials = True  # type: ignore[misc]
    with pytest.raises(ValueError, match="methods"):
        CORSMiddleware(allow_methods=())
    with pytest.raises(ValueError, match="max_age"):
        CORSMiddleware(max_age=86_401)
    with pytest.raises(ValueError, match="max_age"):
        CORSMiddleware(max_age=float("nan"))


def test_cors_policy_collections_and_text_are_bounded() -> None:
    origins = tuple(f"https://client-{index}.example" for index in range(1_025))
    with pytest.raises(ValueError, match="entries"):
        CORSMiddleware(allow_origins=origins)
    with pytest.raises(ValueError, match="characters"):
        CORSMiddleware(allow_headers=("x" * 513,))


@pytest.mark.parametrize(
    "kwargs, error",
    [
        ({"allow_credentials": 1}, TypeError),
        ({"allow_origins": "https://client.example"}, TypeError),
        ({"allow_methods": ("GET", 1)}, TypeError),
        ({"allow_methods": ("GET", "bad method")}, ValueError),
        ({"allow_headers": ("content type",)}, ValueError),
        ({"expose_headers": ("",)}, ValueError),
    ],
)
def test_cors_policy_rejects_malformed_typed_configuration(
    kwargs: dict[str, object], error: type[Exception]
) -> None:
    with pytest.raises(error):
        CORSMiddleware(**kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_cors_rejects_oversized_or_malformed_preflight_header_lists() -> None:
    app = Application(ApplicationConfig(name="cors-preflight-bounds"))
    asgi = ASGIApplication(app)
    asgi.add_middleware(
        CORSMiddleware(
            allow_origins=("https://trusted.example",),
            allow_methods=("GET",),
            allow_headers=("x-request-id",),
        )
    )

    @app.router.route("/data", name="data")
    async def data(request):
        return Response.text("ok")

    oversized = ",".join(f"x-header-{index}" for index in range(1_025))
    async with TestClient(asgi) as client:
        response = await client.request(
            "OPTIONS",
            "/data",
            headers={
                "origin": "https://trusted.example",
                "access-control-request-method": "GET",
                "access-control-request-headers": oversized,
            },
        )
        malformed = await client.request(
            "OPTIONS",
            "/data",
            headers={
                "origin": "https://trusted.example",
                "access-control-request-method": "GET",
                "access-control-request-headers": "x-request-id, bad header",
            },
        )
    assert response.status == 403
    assert malformed.status == 403


@pytest.mark.asyncio
async def test_cors_ignores_an_oversized_request_origin() -> None:
    """A large untrusted Origin is rejected before policy matching work begins."""
    app = Application(ApplicationConfig(name="cors-origin-bound"))
    asgi = ASGIApplication(app)
    asgi.add_middleware(CORSMiddleware(allow_origins=("https://trusted.example",)))

    @app.router.route("/data", name="data")
    async def data(request):
        return Response.text("ok")

    oversized = "https://" + ("a" * 512)
    async with TestClient(asgi) as client:
        response = await client.request("GET", "/data", headers={"origin": oversized})
    assert response.status == 200
    assert "access-control-allow-origin" not in response.headers
