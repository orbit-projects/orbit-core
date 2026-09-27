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
"""Route template matching tests."""

import pytest
from pydantic import BaseModel

from orbit.asgi import Response
from orbit.routing import Route, RouteMetadata, Router
from orbit.types import new_service_id


def test_route_records_validate_direct_construction() -> None:
    metadata = RouteMetadata(name="root", method="GET", path="/")
    route = Route(metadata, lambda request: Response.text("ok"))
    assert route.metadata is metadata
    with pytest.raises(TypeError, match="RouteMetadata"):
        Route(object(), lambda request: Response.text("ok"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="callable"):
        Route(metadata, object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="middleware"):
        Route(
            metadata,
            lambda request: Response.text("ok"),
            middleware=(object(),),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="middleware"):
        Route(
            metadata,
            lambda request: Response.text("ok"),
            middleware=(lambda request, next_handler: next_handler(request),) * 1_025,
        )


def test_route_metadata_rejects_implicit_identity_and_role_coercion() -> None:
    """Route identity and authorization labels must be typed before dispatch."""
    with pytest.raises(ValueError):
        RouteMetadata(name=1, method="GET", path="/")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        RouteMetadata(
            name="root",
            method="GET",
            path="/",
            roles=frozenset({1}),  # type: ignore[arg-type]
        )


async def test_router_extracts_named_path_parameters() -> None:
    """A template route returns its handler and decoded parameter mapping."""
    router = Router()

    async def handler(_: object) -> Response:
        return Response.text("ok")

    route = Route(RouteMetadata(name="user", method="GET", path="/users/{user_id}"), handler)
    router.register(route)

    matched, parameters = router.match("GET", "/users/42")

    assert matched is route
    assert parameters == {"user_id": "42"}


def test_router_generates_openapi_with_path_parameters_and_security():
    router = Router()

    async def handler(_: object) -> Response:
        return Response.text("ok")

    router.route(
        "/users/{user_id}",
        method="GET",
        name="user-detail",
        roles=frozenset({"reader"}),
        api_version="v1",
    )(handler)
    document = router.openapi(title="Example", version="1.2.3")
    operation = document["paths"]["/users/{user_id}"]["get"]
    assert document["openapi"] == "3.1.0"
    assert operation["parameters"][0]["name"] == "user_id"
    assert operation["security"] == [{"bearerAuth": []}]
    assert operation["x-orbit-roles"] == ["reader"]
    assert "422" in operation["responses"]
    assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
    assert document["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    assert "ErrorResponse" in document["components"]["schemas"]
    assert operation["x-orbit-api-version"] == "v1"


def test_route_decorator_retains_typed_service_ownership() -> None:
    """Route registration records the owning service for inspection and validation."""
    router = Router()
    service_id = new_service_id()

    @router.route("/owned", name="owned", service_id=service_id)
    async def owned(request):
        return Response.text("ok")

    assert router.routes[0].metadata.service_id == service_id


def test_grouped_route_retains_typed_service_ownership() -> None:
    """Grouped route registration preserves service metadata alongside inherited roles."""
    router = Router()
    service_id = new_service_id()
    group = router.group("/orders", roles=frozenset({"operator"}))

    @group.route("/{order_id}", name="get", service_id=service_id)
    async def get_order(request):
        return Response.text(request.path_parameters["order_id"])

    metadata = router.routes[0].metadata
    assert metadata.path == "/orders/{order_id}"
    assert metadata.roles == frozenset({"operator"})
    assert metadata.service_id == service_id


def test_openapi_collects_nested_model_definitions():
    class Address(BaseModel):
        city: str

    class User(BaseModel):
        address: Address

    router = Router()

    @router.route("/users", method="POST", name="create-user", request_model=User)
    async def create_user(request):
        return Response.text("ok")

    document = router.openapi()
    content = document["paths"]["/users"]["post"]["requestBody"]["content"]
    schema = content["application/json"]["schema"]
    assert schema["properties"]["address"]["$ref"] == "#/components/schemas/Address"
    assert "Address" in document["components"]["schemas"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"title": ""},
        {"title": "unsafe\napplication"},
        {"title": "x" * 256},
        {"version": "x" * 64},
        {"version": "bad\x7fversion"},
    ],
)
def test_openapi_metadata_is_bounded_and_printable(kwargs: dict[str, str]) -> None:
    """OpenAPI metadata cannot inject unbounded or control-containing document text."""
    with pytest.raises(ValueError, match="OpenAPI"):
        Router().openapi(**kwargs)
