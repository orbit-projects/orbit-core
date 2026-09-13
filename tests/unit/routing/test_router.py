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

from orbit.asgi import Response
from orbit.routing import Route, RouteMetadata, Router


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
