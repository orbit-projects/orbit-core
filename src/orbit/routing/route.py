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
"""One route and its callable handler."""

from dataclasses import dataclass

from pydantic import BaseModel

from orbit._limits import _MAX_RELATION_ENTRIES
from orbit.asgi.middleware import Middleware
from orbit.routing.handler import RouteHandler
from orbit.routing.models import RouteMetadata


@dataclass(frozen=True)
class Route:
    """Validated binding of typed route metadata to a handler implementation."""

    metadata: RouteMetadata
    handler: RouteHandler
    request_model: type[BaseModel] | None = None
    response_model: type[BaseModel] | None = None
    middleware: tuple[Middleware, ...] = ()

    def __post_init__(self) -> None:
        """Validate the route object before a router or adapter receives it."""
        if not isinstance(self.metadata, RouteMetadata):
            raise TypeError("Routes must contain RouteMetadata.")
        if not callable(self.handler):
            raise TypeError("Route handlers must be callable.")
        for model in (self.request_model, self.response_model):
            if model is not None and (
                not isinstance(model, type) or not issubclass(model, BaseModel)
            ):
                raise TypeError("Route models must be Pydantic model classes.")
        if not isinstance(self.middleware, tuple):
            raise TypeError("Route middleware must be a tuple of callables.")
        if len(self.middleware) > _MAX_RELATION_ENTRIES:
            raise ValueError(
                f"Route middleware cannot contain more than {_MAX_RELATION_ENTRIES:,} entries."
            )
        if any(not callable(middleware) for middleware in self.middleware):
            raise TypeError("Route middleware must be callable tuple members.")


__all__ = ["Route"]
