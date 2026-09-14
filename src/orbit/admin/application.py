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
"""Authenticated operational console derived directly from Core models.

No independent database, session cookies or client-side state is introduced. The caller
supplies an Authenticator through ASGIApplication. All views require orbit.admin.read;
health refresh requires orbit.admin.write and an explicit Authorization header.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
from typing import TYPE_CHECKING

from orbit.admin.models import AdminOverview
from orbit.asgi.request import HTTPError, Request
from orbit.asgi.response import Response
from orbit.diagnostics.inspection import inspect_composition
from orbit.security import require_roles
from orbit.security.context import current_principal

if TYPE_CHECKING:
    from orbit.application import Application

_LOG = logging.getLogger(__name__)


class AdminApplication:
    """Render a protected administrative dashboard and structured inspection endpoints."""

    def __init__(self, application: Application) -> None:
        self._application = application

    def overview(self) -> AdminOverview:
        """Inspect shared Core state after checking the current principal's read permission."""
        require_roles(current_principal(), {"orbit.admin.read"})
        return AdminOverview(
            state=self._application.state.application,
            service_names=tuple(d.name for d in self._application.services.descriptors),
        )

    async def handle(self, request: Request) -> Response:
        """Serve authenticated HTML/JSON views or explicitly authorized operational commands."""
        overview = self.overview()
        if request.method == "POST" and request.path == "/admin/health/refresh":
            require_roles(current_principal(), {"orbit.admin.write"})
            # No ambient cookie-only authority for mutations. Browsers with a hostile Origin
            # are rejected even when a custom authenticator also accepts cookies.
            if not request.headers.get("authorization") or request.headers.get("origin"):
                raise HTTPError(
                    403, "security.csrf", "Explicit non-browser authorization required."
                )
            report = await self._application.health()
            return Response.json(report)
        if request.method not in {"GET", "HEAD"}:
            return Response(status=405, headers={"allow": "GET, HEAD"})
        composition = inspect_composition(self._application).model_dump(mode="json")
        views: dict[str, object] = {
            "/admin/lifecycle": [
                transition.model_dump(mode="json")
                for transition in self._application.lifecycle.history
            ],
            "/admin/health": {
                "status": self._application.state.application.health,
                "services": [
                    {"name": state.name, "status": state.health}
                    for state in self._application.state.application.services
                ],
            },
            "/admin/diagnostics": self._application.diagnostics.collect(
                self._application
            ).model_dump(mode="json"),
            "/admin/state": overview.model_dump(mode="json"),
            "/admin/config": composition["configuration"],
            "/admin/services": composition["services"],
            "/admin/plugins": composition["plugins"],
            "/admin/routes": composition["routes"],
            "/admin/dependencies": composition["dependencies"],
            "/admin/events": [
                {
                    "id": str(d.event_id),
                    "name": d.name,
                    "subscribers": d.subscriber_count,
                    "failures": d.failures,
                }
                for d in self._application.events.history
            ],
        }
        headers = {
            "cache-control": "no-store",
            "x-frame-options": "DENY",
            "content-security-policy": (
                "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'"
            ),
        }
        for name, contribution in self._application.admin_contributions.items():
            path = "/admin/extensions/" + name
            if request.path in {"/admin", "/admin/", path}:
                try:
                    async with asyncio.timeout(self._application.config.application.health_timeout):
                        views[path] = (await contribution.inspect()).model_dump(mode="json")
                except Exception:
                    _LOG.exception("Administrative extension inspection failed")
                    views[path] = {"status": "unavailable", "code": "admin.extension-failed"}
        if request.path in views:
            response = Response.json(views[request.path])
            return Response(
                response.status, response.body, {**headers, "content-type": "application/json"}
            )
        if request.path not in {"/admin", "/admin/"}:
            return Response.json({"code": "routing.route-not-found"}, status=404)
        name = html.escape(self._application.config.application.name)
        sections = "".join(
            "<section><h2>"
            + html.escape(path.removeprefix("/admin/").title())
            + "</h2><pre>"
            + html.escape(json.dumps(value, indent=2))
            + "</pre></section>"
            for path, value in views.items()
        )
        body = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>Orbit Administration</title><style>"
            "body{font:16px system-ui;background:#111827;color:#e5e7eb;"
            "margin:2rem auto;max-width:1000px}h1{color:#a5b4fc}"
            "section{padding:1rem;background:#1f2937;margin:1rem 0;border-radius:8px}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}"
            "</style></head><body><h1>Orbit · " + name + "</h1>"
            "<p>Application state, services and runtime diagnostics</p>"
            + sections
            + "</body></html>"
        )
        return Response(200, body.encode(), {**headers, "content-type": "text/html; charset=utf-8"})


__all__ = ["AdminApplication"]
