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

import html
import json
import logging
import re
from dataclasses import asdict
from typing import TYPE_CHECKING

from pydantic import BaseModel

from orbit._limits import safe_exception_type_name
from orbit.admin.models import AdminOverview
from orbit.asgi.request import Headers, HTTPError, Request
from orbit.asgi.response import Response
from orbit.diagnostics.inspection import inspect_composition
from orbit.security import require_roles
from orbit.security.context import current_principal

if TYPE_CHECKING:
    from orbit.application import Application

_LOG = logging.getLogger(__name__)
_ADMIN_TARGET_NAME = re.compile(r"[a-z][a-z0-9-]{0,62}")


def _audit_error_code(error: Exception) -> str:
    """Return a valid audit identifier without trusting custom exception attributes."""
    problem = getattr(error, "problem", None)
    code = getattr(problem, "code", None)
    if isinstance(code, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,126}", code):
        return code
    return safe_exception_type_name(error)


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

    @staticmethod
    def _has_explicit_authorization(request: Request) -> bool:
        """Require one nonempty authorization field and no browser Origin for mutations."""
        if not isinstance(request.headers, Headers):
            raise TypeError("Admin mutations require Core Headers.")
        values = request.headers.getall("authorization")
        return len(values) == 1 and bool(values[0].strip()) and not request.headers.getall("origin")

    async def handle(self, request: Request) -> Response:
        """Serve authenticated HTML/JSON views or explicitly authorized operational commands."""
        principal = current_principal()
        identity = principal.identity if principal is not None else None
        key = f"{identity.provider}:{identity.subject}" if identity is not None else "anonymous"
        decision = self._application.admin_rate_limiter.check(key)
        if not decision.allowed:
            response = Response.json(
                {"code": "security.rate-limited", "retry_after": decision.retry_after},
                status=429,
            )
            if not isinstance(response.headers, Headers):
                raise TypeError("Responses must expose validated headers.")
            return Response(
                response.status,
                response.body,
                {**response.headers, "retry-after": str(max(1, int(decision.retry_after + 0.999)))},
            )
        overview = self.overview()
        service_prefix = "/admin/services/"
        task_prefix = "/admin/tasks/"
        if request.method == "POST" and request.path.startswith(task_prefix):
            require_roles(current_principal(), {"orbit.admin.write"})
            if not self._has_explicit_authorization(request):
                raise HTTPError(
                    403, "security.csrf", "Explicit non-browser authorization required."
                )
            remainder = request.path[len(task_prefix) :].strip("/")
            name, separator, action = remainder.rpartition("/")
            if not separator or action != "restart" or not name:
                return Response.json({"code": "tasks.invalid-operation"}, status=400)
            if _ADMIN_TARGET_NAME.fullmatch(name) is None:
                return Response.json({"code": "tasks.invalid-name"}, status=400)
            try:
                await self._application.restart_task(name)
            except KeyError:
                return Response.json({"code": "tasks.not-found"}, status=404)
            except Exception as exc:
                self._application.record_admin_audit(
                    "task.restart",
                    target=name,
                    success=False,
                    error_code=_audit_error_code(exc),
                )
                raise
            self._application.record_admin_audit("task.restart", target=name, success=True)
            return Response.json({"status": "restarted", "task": name})
        if request.method == "POST" and request.path.startswith(service_prefix):
            require_roles(current_principal(), {"orbit.admin.write"})
            if not self._has_explicit_authorization(request):
                raise HTTPError(
                    403, "security.csrf", "Explicit non-browser authorization required."
                )
            remainder = request.path[len(service_prefix) :].strip("/")
            name, separator, action = remainder.rpartition("/")
            if not separator or action not in {"start", "stop", "restart", "reload"}:
                return Response.json({"code": "services.invalid-operation"}, status=400)
            if not name or _ADMIN_TARGET_NAME.fullmatch(name) is None:
                return Response.json({"code": "services.invalid-name"}, status=400)
            try:
                if action == "start":
                    await self._application.start_service(name)
                elif action == "stop":
                    await self._application.stop_service(name)
                elif action == "restart":
                    await self._application.restart_service(name)
                else:
                    await self._application.reload_service(name)
            except KeyError:
                return Response.json({"code": "services.not-found"}, status=404)
            except Exception as exc:
                self._application.record_admin_audit(
                    f"service.{action}",
                    target=name,
                    success=False,
                    error_code=_audit_error_code(exc),
                )
                raise
            status = {
                "start": "started",
                "stop": "stopped",
                "restart": "restarted",
                "reload": "reloaded",
            }[action]
            self._application.record_admin_audit(f"service.{action}", target=name, success=True)
            return Response.json({"status": status, "service": name})
        if request.method == "POST" and request.path == "/admin/health/refresh":
            require_roles(current_principal(), {"orbit.admin.write"})
            # No ambient cookie-only authority for mutations. Browsers with a hostile Origin
            # are rejected even when a custom authenticator also accepts cookies.
            if not self._has_explicit_authorization(request):
                raise HTTPError(
                    403, "security.csrf", "Explicit non-browser authorization required."
                )
            try:
                report = await self._application.health()
            except Exception as exc:
                self._application.record_admin_audit(
                    "health.refresh",
                    success=False,
                    error_code=_audit_error_code(exc),
                )
                raise
            self._application.record_admin_audit("health.refresh", success=True)
            return Response.json(report)
        if request.method not in {"GET", "HEAD"}:
            return Response(status=405, headers={"allow": "GET, HEAD"})
        composition = inspect_composition(self._application).model_dump(mode="json")
        views: dict[str, object] = {
            "/admin/lifecycle": [
                transition.model_dump(mode="json")
                for transition in self._application.lifecycle.history
            ],
            "/admin/audit": [
                record.model_dump(mode="json") for record in self._application.admin_audit
            ],
            "/admin/health": {
                "status": self._application.state.application.health,
                "history": [
                    report.model_dump(mode="json") for report in self._application.health_history
                ],
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
            "/admin/tasks": [
                {
                    "name": task.name,
                    "state": task.state,
                    "attempts": task.attempts,
                    "last_failure": (
                        asdict(task.last_failure) if task.last_failure is not None else None
                    ),
                }
                for task in self._application.tasks.infos
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
                    view = await self._application._inspect_admin_contribution(  # noqa: SLF001
                        name,
                        contribution,
                        self._application.config.application.health_timeout,
                    )
                    if view is None:
                        raise TimeoutError("Administrative extension inspection is still pending.")
                    if not isinstance(view, BaseModel):
                        raise TypeError("Admin contributions must return Pydantic models.")
                    views[path] = view.model_dump(mode="json")
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
