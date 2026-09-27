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
"""Deterministic path-first routing with HEAD, OPTIONS and ambiguity checks."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from orbit._limits import _MAX_CORE_CAPACITY, MAX_PATH_BYTES
from orbit.asgi.middleware import Middleware
from orbit.errors import ErrorCategory, ErrorResponse, OrbitProblem, RoutingError
from orbit.routing.handler import RouteHandler
from orbit.routing.models import RouteMetadata
from orbit.routing.route import Route
from orbit.security.roles import validate_role_collection
from orbit.types import ServiceId

_SUPPORTED_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


def _error(code: str, message: str, **context: object) -> RoutingError:
    return RoutingError(
        OrbitProblem(
            code="routing." + code, message=message, category=ErrorCategory.ROUTING, context=context
        )
    )


def _parts(path: str) -> tuple[str, ...]:
    return tuple(path.split("/")[1:])


def _validate_dispatch_path(path: str) -> None:
    """Keep direct router calls on the same canonical path contract as ASGI dispatch."""
    if not isinstance(path, str):
        raise _error("invalid-path", "Request path is not canonical.")
    try:
        path_size = len(path.encode("utf-8"))
    except UnicodeEncodeError:
        raise _error("invalid-path", "Request path is not canonical.") from None
    if (
        path_size > MAX_PATH_BYTES
        or not path.startswith("/")
        or "//" in path
        or "\\" in path
        or "?" in path
        or "#" in path
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        raise _error("invalid-path", "Request path is not canonical.")


def _parameter(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


class Router:
    """Match static paths before parameter paths; trailing slashes remain significant."""

    def __init__(self) -> None:
        self._routes: list[Route] = []
        self._frozen = False

    @property
    def routes(self) -> tuple[Route, ...]:
        """Return registered routes in composition order."""
        return tuple(self._routes)

    def freeze(self) -> None:
        """Prevent runtime route mutation."""
        self._frozen = True

    def register(self, route: Route) -> None:
        """Reject duplicate identifiers/names and equivalent parameter templates."""
        if self._frozen:
            raise _error("frozen", "Routing is frozen.")
        if len(self._routes) >= _MAX_CORE_CAPACITY:
            raise _error("capacity", "Route registry capacity reached.")
        if not isinstance(route, Route) or not isinstance(route.metadata, RouteMetadata):
            raise _error("invalid-route", "Routes must contain RouteMetadata.")
        if not callable(route.handler):
            raise _error("invalid-handler", "Route handlers must be callable.")
        for model in (route.request_model, route.response_model):
            if model is not None and (
                not isinstance(model, type) or not issubclass(model, BaseModel)
            ):
                raise _error("invalid-model", "Route models must be Pydantic model classes.")
        if not isinstance(route.middleware, tuple) or any(
            not callable(middleware) for middleware in route.middleware
        ):
            raise _error("invalid-middleware", "Route middleware must be callable tuple members.")
        shape = tuple("{}" if _parameter(s) else s for s in _parts(route.metadata.path))
        for existing in self._routes:
            old = existing.metadata
            old_shape = tuple("{}" if _parameter(s) else s for s in _parts(old.path))
            if (
                old.name == route.metadata.name
                or old.id == route.metadata.id
                or (old.method == route.metadata.method and shape == old_shape)
            ):
                raise _error("duplicate-route", f"Duplicate/ambiguous route {route.metadata.name}.")
        self._routes.append(route)

    def route(
        self,
        path: str,
        *,
        method: str = "GET",
        name: str,
        service_id: ServiceId | None = None,
        roles: frozenset[str] = frozenset(),
        request_model: type[BaseModel] | None = None,
        response_model: type[BaseModel] | None = None,
        middleware: tuple[Middleware, ...] = (),
        api_version: str | None = None,
    ) -> Callable[[RouteHandler], RouteHandler]:
        """Register a handler and optional owning service identity.

        ``service_id`` is metadata only: dispatch remains owned by the router, while application
        composition validates that the referenced service is registered. Keeping ownership on the
        route lets inspection, administration, and plugins reason about service boundaries without
        coupling handlers to a concrete service implementation.
        """

        def register(handler: RouteHandler) -> RouteHandler:
            """Create and register route metadata for the decorated handler."""
            self.register(
                Route(
                    RouteMetadata(
                        path=path,
                        method=method,
                        name=name,
                        service_id=service_id,
                        roles=roles,
                        api_version=api_version,
                    ),
                    handler,
                    request_model,
                    response_model,
                    middleware,
                )
            )
            return handler

        return register

    def group(
        self,
        prefix: str,
        *,
        name_prefix: str = "",
        roles: frozenset[str] = frozenset(),
    ) -> RouteGroup:
        """Create a route group with a shared path prefix and authorization roles.

        Role collections are normalized at composition time so nested groups can safely combine
        them and route metadata always receives an immutable, validated set of identifiers.
        """
        if not isinstance(prefix, str):
            raise TypeError("Group prefixes must be strings.")
        try:
            prefix_size = len(prefix.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValueError("Group prefixes must be valid Unicode paths.") from exc
        if (
            prefix_size > MAX_PATH_BYTES
            or not prefix.startswith("/")
            or "//" in prefix
            or "\\" in prefix
            or "?" in prefix
            or "#" in prefix
            or prefix.endswith("/")
            and prefix != "/"
            or any(ord(character) < 32 or ord(character) == 127 for character in prefix)
            or any(segment in {".", ".."} for segment in prefix.split("/"))
        ):
            raise ValueError("Group prefixes must be canonical absolute paths.")
        if not isinstance(name_prefix, str) or len(name_prefix) > 255:
            raise ValueError("Route group name prefixes must be bounded strings.")
        if any(ord(character) < 32 or ord(character) == 127 for character in name_prefix):
            raise ValueError("Route group name prefixes must be printable.")
        if name_prefix and not name_prefix.endswith("-"):
            name_prefix += "-"
        return RouteGroup(
            self,
            prefix.rstrip("/") or "",
            name_prefix,
            validate_role_collection(roles),
        )

    def _candidates(self, path: str) -> list[tuple[Route, dict[str, str]]]:
        _validate_dispatch_path(path)
        actual = _parts(path)
        matches: list[tuple[tuple[bool, ...], Route, dict[str, str]]] = []
        for route in self._routes:
            expected = _parts(route.metadata.path)
            if len(actual) != len(expected):
                continue
            parameters: dict[str, str] = {}
            for pattern, value in zip(expected, actual, strict=True):
                if _parameter(pattern) and value:
                    parameters[pattern[1:-1]] = value
                elif pattern != value:
                    break
            else:
                specificity = tuple(not _parameter(s) for s in expected)
                matches.append((specificity, route, parameters))
        if not matches:
            return []
        best = max(item[0] for item in matches)
        return [(route, params) for score, route, params in matches if score == best]

    def allowed_methods(self, path: str) -> tuple[str, ...]:
        """Return supported methods for the best matching path, including implicit methods."""
        methods = {r.metadata.method for r, _ in self._candidates(path)}
        if "GET" in methods:
            methods.add("HEAD")
        if methods:
            methods.add("OPTIONS")
        return tuple(sorted(methods))

    def openapi(
        self, *, title: str = "Orbit Application", version: str = "0.1.0"
    ) -> dict[str, Any]:
        """Build a deterministic OpenAPI 3.1 document from registered route metadata."""
        for field_name, value, maximum in (("title", title, 255), ("version", version, 63)):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= maximum
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError(f"OpenAPI {field_name} must be bounded printable text.")
        paths: dict[str, dict[str, Any]] = {}
        schemas: dict[str, Any] = {"ErrorResponse": ErrorResponse.model_json_schema()}

        def model_schema(model: type[BaseModel]) -> dict[str, Any]:
            """Convert a Pydantic model and merge definitions into OpenAPI components."""
            schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
            for name, definition in schema.pop("$defs", {}).items():
                existing = schemas.get(name)
                if existing is not None and existing != definition:
                    raise ValueError(f"Conflicting OpenAPI schema definition: {name}.")
                schemas[name] = definition
            return schema

        for route in self._routes:
            metadata = route.metadata
            parameters = [
                {
                    "name": segment[1:-1],
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
                for segment in _parts(metadata.path)
                if _parameter(segment)
            ]
            operation: dict[str, Any] = {
                "operationId": metadata.name,
                "summary": metadata.summary,
                "parameters": parameters,
                "responses": {
                    "200": {"description": "Successful response."},
                    **{
                        str(status): {
                            "description": description,
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            },
                        }
                        for status, description in {
                            400: "Invalid request.",
                            401: "Authentication required.",
                            403: "Insufficient permissions.",
                            404: "Resource not found.",
                            405: "Method not allowed.",
                            422: "Request validation failed.",
                            500: "Internal server error.",
                        }.items()
                    },
                },
            }
            if metadata.roles:
                operation["security"] = [{"bearerAuth": []}]
                operation["x-orbit-roles"] = sorted(metadata.roles)
            if metadata.api_version is not None:
                operation["x-orbit-api-version"] = metadata.api_version
            if route.request_model is not None:
                operation["requestBody"] = {
                    "required": True,
                    "content": {"application/json": {"schema": model_schema(route.request_model)}},
                }
            if route.response_model is not None:
                operation["responses"]["200"]["content"] = {
                    "application/json": {"schema": model_schema(route.response_model)}
                }
            paths.setdefault(metadata.path, {})[metadata.method.lower()] = operation
        return {
            "openapi": "3.1.0",
            "info": {"title": title, "version": version},
            "paths": paths,
            "components": {
                "securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer"}},
                "schemas": schemas,
            },
        }

    def match(self, method: str, path: str) -> tuple[Route, dict[str, str]]:
        """Resolve a request or distinguish missing path (404) from disallowed method (405)."""
        if not isinstance(method, str):
            raise _error("invalid-method", "Request method must be a string.")
        method = method.upper()
        if method not in _SUPPORTED_METHODS:
            raise _error("invalid-method", "Unsupported HTTP method.")
        candidates = self._candidates(path)
        for route, parameters in candidates:
            if route.metadata.method == method:
                return route, parameters
        if method == "HEAD":
            for route, parameters in candidates:
                if route.metadata.method == "GET":
                    return route, parameters
        if candidates:
            raise _error(
                "method-not-allowed",
                "Method is not allowed.",
                allow=", ".join(self.allowed_methods(path)),
            )
        raise _error("route-not-found", "Route was not found.")


class RouteGroup:
    """Composable route registration scope returned by :meth:`Router.group`."""

    def __init__(
        self, router: Router, prefix: str, name_prefix: str, roles: frozenset[str]
    ) -> None:
        self._router = router
        self._prefix = prefix
        self._name_prefix = name_prefix
        self._roles = roles

    def group(
        self,
        prefix: str,
        *,
        name_prefix: str = "",
        roles: frozenset[str] = frozenset(),
    ) -> RouteGroup:
        """Create a nested group inheriting this group's prefix and roles."""
        child = self._router.group(prefix, name_prefix=name_prefix, roles=roles)
        child._prefix = self._prefix + child._prefix
        child._name_prefix = self._name_prefix + child._name_prefix
        child._roles = self._roles | child._roles
        return child

    def route(
        self,
        path: str,
        *,
        method: str = "GET",
        name: str,
        service_id: ServiceId | None = None,
        roles: frozenset[str] = frozenset(),
        request_model: type[BaseModel] | None = None,
        response_model: type[BaseModel] | None = None,
        middleware: tuple[Middleware, ...] = (),
        api_version: str | None = None,
    ) -> Callable[[RouteHandler], RouteHandler]:
        """Register a route under this group's prefix."""
        if not isinstance(path, str):
            raise TypeError("Grouped route paths must be strings.")
        if not path.startswith("/"):
            raise ValueError("Grouped route paths must be absolute.")
        full_path = self._prefix + path if self._prefix else path
        normalized_roles = validate_role_collection(roles)
        return self._router.route(
            full_path,
            method=method,
            name=self._name_prefix + name,
            service_id=service_id,
            roles=self._roles | normalized_roles,
            request_model=request_model,
            response_model=response_model,
            middleware=middleware,
            api_version=api_version,
        )


__all__ = ["RouteGroup", "Router"]
