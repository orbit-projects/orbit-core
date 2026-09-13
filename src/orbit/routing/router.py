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

from orbit.errors import ErrorCategory, OrbitProblem, RoutingError
from orbit.routing.handler import RouteHandler
from orbit.routing.models import RouteMetadata
from orbit.routing.route import Route


def _error(code: str, message: str, **context: object) -> RoutingError:
    return RoutingError(
        OrbitProblem(
            code="routing." + code, message=message, category=ErrorCategory.ROUTING, context=context
        )
    )


def _parts(path: str) -> tuple[str, ...]:
    return tuple(path.split("/")[1:])


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
        self, path: str, *, method: str = "GET", name: str, roles: frozenset[str] = frozenset()
    ) -> Callable[[RouteHandler], RouteHandler]:
        """Register a handler using a decorator; the original callable is returned."""

        def register(handler: RouteHandler) -> RouteHandler:
            self.register(
                Route(RouteMetadata(path=path, method=method, name=name, roles=roles), handler)
            )
            return handler

        return register

    def _candidates(self, path: str) -> list[tuple[Route, dict[str, str]]]:
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

    def match(self, method: str, path: str) -> tuple[Route, dict[str, str]]:
        """Resolve a request or distinguish missing path (404) from disallowed method (405)."""
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


__all__ = ["Router"]
