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
"""Route registration invariants, path semantics and service boundaries."""

import pytest

from orbit.asgi import Response
from orbit.errors import RoutingError
from orbit.routing import Route, RouteMetadata, Router


async def handler(request):
    return Response.text("ok")


@pytest.mark.parametrize(
    "path",
    ["relative", "/a/{bad-name}", "/a/{x}/{x}", "/a//b", "/a/../b", "/a?query", "/a/{x}tail"],
)
def test_invalid_route_templates(path):
    with pytest.raises(ValueError):
        RouteMetadata(name="route", path=path, method="GET")


def test_equivalent_templates_are_rejected():
    router = Router()
    router.register(Route(RouteMetadata(name="one", path="/a/{id}", method="GET"), handler))
    with pytest.raises(RoutingError):
        router.register(Route(RouteMetadata(name="two", path="/a/{name}", method="GET"), handler))


def test_static_paths_take_precedence_independent_of_registration():
    router = Router()
    router.register(Route(RouteMetadata(name="dynamic", path="/a/{id}", method="GET"), handler))
    router.register(Route(RouteMetadata(name="static", path="/a/current", method="GET"), handler))
    assert router.match("GET", "/a/current")[0].metadata.name == "static"
    with pytest.raises(RoutingError):
        router.match("GET", "/a/current/")
    assert router.allowed_methods("/absent") == ()
    router.freeze()
    with pytest.raises(RoutingError):
        router.register(Route(RouteMetadata(name="late", path="/late", method="GET"), handler))
