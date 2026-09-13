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
"""In-process ASGI harness that owns the real lifespan protocol and captures HTTP messages."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from orbit.asgi.application import ASGIApplication
from orbit.asgi.request import Headers
from orbit.asgi.types import Message


@dataclass(frozen=True)
class TestResponse:
    """Captured HTTP status, repeated headers and body bytes."""

    status: int
    headers: Headers
    body: bytes

    def json(self) -> Any:
        """Decode response JSON for assertions."""
        return json.loads(self.body)


class TestClient:
    """Exercise Core's ASGI callable without sockets or a separate web framework.

    Use async with to send real lifespan messages. Application startup/shutdown failures
    fail the context manager, rather than silently ignoring the protocol.
    """

    __test__ = False

    def __init__(self, application: ASGIApplication) -> None:
        self.application = application
        self._incoming: asyncio.Queue[Message] = asyncio.Queue()
        self._outgoing: asyncio.Queue[Message] = asyncio.Queue()
        self._lifespan: asyncio.Task[None] | None = None

    async def __aenter__(self) -> TestClient:
        self._lifespan = asyncio.create_task(
            self.application({"type": "lifespan"}, self._incoming.get, self._outgoing.put)
        )
        await self._incoming.put({"type": "lifespan.startup"})
        try:
            async with asyncio.timeout(60):
                response = await self._outgoing.get()
            if response["type"] != "lifespan.startup.complete":
                raise RuntimeError(response.get("message", "Startup failed."))
        except BaseException:
            self._lifespan.cancel()
            await asyncio.gather(self._lifespan, return_exceptions=True)
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._lifespan is not None
        await self._incoming.put({"type": "lifespan.shutdown"})
        try:
            async with asyncio.timeout(60):
                response = await self._outgoing.get()
                await self._lifespan
            if response["type"] != "lifespan.shutdown.complete":
                raise RuntimeError(response.get("message", "Shutdown failed."))
        finally:
            if not self._lifespan.done():
                self._lifespan.cancel()
                await asyncio.gather(self._lifespan, return_exceptions=True)

    async def request(
        self, method: str, path: str, *, body: bytes = b"", headers: Mapping[str, str] | None = None
    ) -> TestResponse:
        """Send a complete buffered HTTP request and verify response framing."""
        url = urlsplit(path)
        messages: list[Message] = []
        received = False

        async def receive() -> Message:
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: Message) -> None:
            messages.append(message)

        await self.application(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "method": method,
                "path": url.path,
                "root_path": "",
                "query_string": url.query.encode(),
                "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
            },
            receive,
            send,
        )
        if not messages or messages[0]["type"] != "http.response.start":
            raise AssertionError("ASGI application did not start a response.")
        if messages[-1].get("more_body", False):
            raise AssertionError("ASGI response stream did not finish.")
        return TestResponse(
            messages[0]["status"],
            Headers([(k.decode(), v.decode()) for k, v in messages[0]["headers"]]),
            b"".join(m.get("body", b"") for m in messages[1:]),
        )


__all__ = ["TestClient", "TestResponse"]
