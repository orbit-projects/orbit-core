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
"""HTTP ASGI runtime with bounded requests, scoped dependencies and authenticated dispatch."""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import replace
from functools import partial
from time import monotonic
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic import ValidationError

from orbit.asgi.lifespan import handle_lifespan
from orbit.asgi.middleware import Middleware, NextHandler
from orbit.asgi.request import Headers, HTTPError, Request
from orbit.asgi.response import Response
from orbit.asgi.types import Receive, Scope, Send
from orbit.diagnostics.models import RequestRecord
from orbit.errors import RoutingError, SecurityError
from orbit.lifecycle import LifecyclePhase
from orbit.routing import Router
from orbit.security.authorization import require_roles
from orbit.security.context import bind_principal, current_principal, reset_principal
from orbit.security.contracts import Authenticator

if TYPE_CHECKING:
    from orbit.application import Application
_LOG = logging.getLogger(__name__)


class _Disconnected(Exception):
    """Internal control flow: the peer disconnected before request processing."""


class ASGIApplication:
    """Serve HTTP and lifespan using one composed Core application.

    The host must support lifespan. Requests are bounded by size, duration and concurrency.
    Authentication is explicit; there is no permissive fallback for admin routes. Request
    scopes live through streaming responses and close on success, failure or disconnect.
    """

    def __init__(
        self,
        application: Application,
        router: Router | None = None,
        *,
        authenticator: Authenticator | None = None,
    ) -> None:
        self._application = application
        self._router = router if router is not None else application.router
        self._authenticator = authenticator
        self._middleware: list[Middleware] = []
        self._requests: set[asyncio.Task[Any]] = set()
        self._draining = False

    @property
    def router(self) -> Router:
        """Expose the application's shared router."""
        return self._router

    def add_middleware(self, middleware: Middleware) -> None:
        """Append middleware during composition; first registered executes outermost."""
        self._application.lifecycle.require(LifecyclePhase.CREATED)
        self._middleware.append(middleware)

    async def shutdown(self) -> None:
        """Reject new work, drain bounded requests, then close application resources."""
        self._draining = True
        pending = tuple(self._requests)
        if pending:
            _, unfinished = await asyncio.wait(
                pending, timeout=self._application.config.application.lifecycle_timeout
            )
            for task in unfinished:
                task.cancel()
            if unfinished:
                await asyncio.gather(*unfinished, return_exceptions=True)
        await self._application.stop()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI scope and preserve cancellation/disconnect cleanup semantics."""
        kind = scope["type"]
        if kind == "lifespan":
            self._router.freeze()
            await handle_lifespan(self._application, receive, send, shutdown=self.shutdown)
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 1003})
            return
        if kind != "http":
            raise RuntimeError(f"Unsupported ASGI scope: {kind}")
        config = self._application.config.application
        if self._draining or len(self._requests) >= config.max_concurrent_requests:
            await self._send(
                Response.json({"code": "runtime.unavailable"}, status=503),
                scope.get("method", "GET"),
                send,
            )
            return
        task = asyncio.current_task()
        assert task is not None
        self._requests.add(task)
        request_id = str(uuid4())
        begin = monotonic()
        status = 499
        started = False

        async def tracked_send(message: dict[str, Any]) -> None:
            nonlocal started, status
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                message["headers"].extend(
                    [
                        (b"x-request-id", request_id.encode()),
                        (b"x-content-type-options", b"nosniff"),
                    ]
                )
            try:
                await send(message)
            except OSError as exc:
                raise _Disconnected from exc

        # Runtime/security context is always restored, including on authentication failures.
        principal_token = bind_principal(None)
        try:
            from orbit.runtime.context import bind_application, reset_application

            application_token = bind_application(self._application)
            try:
                async with asyncio.timeout(config.request_timeout):
                    request = await self._read_request(scope, receive, request_id)
                    async with self._application.container.scope() as container:
                        request = replace(request, container=container)
                        if self._authenticator is not None:
                            principal = await self._authenticator.authenticate(request)
                            bind_principal(principal)
                        response = await self._dispatch(request)
                        await self._send(response, request.method, tracked_send)
            finally:
                reset_application(application_token)
        except _Disconnected:
            return
        except Exception as exc:
            if started:
                _LOG.exception("Response interrupted", extra={"request_id": request_id})
                raise
            if isinstance(exc, HTTPError):
                status, code, message = exc.status, exc.code, str(exc)
            elif isinstance(exc, ValidationError):
                status, code, message = 422, "request.validation", "Request validation failed."
            elif isinstance(exc, SecurityError):
                status = 401 if current_principal() is None else 403
                code, message = "security.forbidden", "Authentication or authorization required."
            elif isinstance(exc, TimeoutError):
                status, code, message = 504, "request.timeout", "Request deadline exceeded."
            else:
                _LOG.exception("Unhandled request failure", extra={"request_id": request_id})
                status, code, message = 500, "runtime.internal", "Internal server error."
            await self._send(
                Response.json(
                    {"code": code, "message": message, "request_id": request_id}, status=status
                ),
                scope.get("method", "GET"),
                tracked_send,
            )
        finally:
            reset_principal(principal_token)
            self._requests.discard(task)
            self._application.diagnostics.record_request(
                RequestRecord(
                    request_id=request_id,
                    method=scope["method"],
                    status=status,
                    duration_seconds=monotonic() - begin,
                )
            )

    async def _read_request(self, scope: Scope, receive: Receive, request_id: str) -> Request:
        limit = self._application.config.application.max_body_bytes
        headers = Headers(
            tuple(
                (key.decode("latin-1"), value.decode("latin-1"))
                for key, value in scope.get("headers", [])
            )
        )
        lengths = headers.getall("content-length")
        if (
            len(lengths) > 1
            or (lengths and not lengths[0].isascii())
            or (lengths and not lengths[0].isdigit())
        ):
            raise HTTPError(400, "request.content-length", "Invalid Content-Length.")
        if lengths and int(lengths[0]) > limit:
            raise HTTPError(413, "request.too-large", "Request body is too large.")
        chunks = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                raise _Disconnected
            if message["type"] != "http.request":
                raise HTTPError(400, "request.protocol", "Unexpected request message.")
            body = message.get("body", b"")
            if len(chunks) + len(body) > limit:
                raise HTTPError(413, "request.too-large", "Request body is too large.")
            chunks.extend(body)
            if not message.get("more_body", False):
                break
        if lengths and len(chunks) != int(lengths[0]):
            raise HTTPError(400, "request.content-length", "Content-Length does not match body.")
        path = scope["path"]
        root = scope.get("root_path", "")
        if root and (path == root or path.startswith(root + "/")):
            path = path[len(root) :] or "/"
        return Request(
            method=scope["method"],
            path=path,
            headers=headers,
            body=bytes(chunks),
            query_string=scope.get("query_string", b""),
            request_id=request_id,
            root_path=root,
        )

    async def _dispatch(self, request: Request) -> Response:
        async def endpoint(current: Request) -> Response:
            if current.path in {"/health/live", "/health/ready"}:
                if current.method not in {"GET", "HEAD"}:
                    return Response(status=405, headers={"allow": "GET, HEAD"})
                if current.path == "/health/live":
                    return Response.json({"status": "healthy"})
                report = await self._application.health()
                return Response.json(
                    {"status": report.status}, status=200 if self._application.is_ready else 503
                )
            if self._application.lifecycle.phase is not LifecyclePhase.RUNNING:
                return Response.json({"code": "runtime.not-ready"}, status=503)
            if current.path == "/admin" or current.path.startswith("/admin/"):
                if not self._application.config.application.admin_enabled:
                    return Response.json({"code": "routing.route-not-found"}, status=404)
                from orbit.admin.application import AdminApplication

                return await AdminApplication(self._application).handle(current)
            allowed = self._router.allowed_methods(current.path)
            if current.method == "OPTIONS" and allowed:
                return Response(status=204, headers={"allow": ", ".join(allowed)})
            try:
                route, parameters = self._router.match(current.method, current.path)
            except RoutingError as exc:
                if exc.problem.code == "routing.method-not-allowed":
                    return (
                        Response.json({"code": exc.problem.code}, status=405)
                        if not allowed
                        else (Response(status=405, headers={"allow": ", ".join(allowed)}))
                    )
                return Response.json({"code": exc.problem.code}, status=404)
            if route.metadata.roles:
                require_roles(current_principal(), route.metadata.roles)
            result = route.handler(replace(current, path_parameters=parameters))
            response = await result if inspect.isawaitable(result) else result
            if not isinstance(response, Response):
                raise TypeError("Route handlers must return Response.")
            return response

        handler: NextHandler = endpoint
        for middleware in reversed(self._middleware):
            handler = partial(middleware, next_handler=handler)
        return await handler(request)

    @staticmethod
    async def _send(response: Response, method: str, send: Send) -> None:
        try:
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status,
                    "headers": response.wire_headers(),
                }
            )
            if method == "HEAD" or response.status in {204, 304}:
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            elif response.stream is not None:
                async for chunk in response.stream:
                    if not isinstance(chunk, bytes):
                        raise TypeError("Response streams must yield bytes.")
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                await send({"type": "http.response.body", "body": b"", "more_body": False})
            else:
                await send(
                    {"type": "http.response.body", "body": response.body, "more_body": False}
                )
        finally:
            if response.stream is not None:
                close = getattr(response.stream, "aclose", None)
                if close is not None:
                    await close()


__all__ = ["ASGIApplication"]
