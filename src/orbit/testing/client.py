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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from orbit._limits import is_finite_number
from orbit.asgi.application import ASGIApplication
from orbit.asgi.request import Headers
from orbit.asgi.types import Message


@dataclass(frozen=True)
class TestResponse:
    """Captured HTTP status, repeated headers and body bytes."""

    status: int
    headers: Headers
    body: bytes

    def __post_init__(self) -> None:
        """Validate captured response data even when a test constructs it directly."""
        if (
            isinstance(self.status, bool)
            or not isinstance(self.status, int)
            or not 200 <= self.status <= 599
        ):
            raise ValueError("Test response status must be final (200..599).")
        if not isinstance(self.headers, Headers):
            raise TypeError("Test response headers must be validated Headers.")
        if not isinstance(self.body, bytes):
            raise TypeError("Test response body must be bytes.")

    def json(self) -> Any:
        """Decode response JSON for assertions."""
        return json.loads(self.body)


class TestClient:
    """Exercise Core's ASGI callable without sockets or a separate web framework.

    Use async with to send real lifespan messages. Application startup/shutdown failures
    fail the context manager, rather than silently ignoring the protocol.
    """

    __test__ = False

    def __init__(self, application: ASGIApplication, *, lifespan_timeout: float = 60) -> None:
        if (
            isinstance(lifespan_timeout, bool)
            or not isinstance(lifespan_timeout, (int, float))
            or not is_finite_number(lifespan_timeout)
            or lifespan_timeout <= 0
        ):
            raise ValueError("lifespan_timeout must be finite and positive.")
        self.application = application
        self._timeout = lifespan_timeout
        self._active = False
        self._incoming: asyncio.Queue[Message] = asyncio.Queue()
        self._outgoing: asyncio.Queue[Message] = asyncio.Queue()
        self._lifespan: asyncio.Task[None] | None = None

    async def __aenter__(self) -> TestClient:
        if self._lifespan is not None:
            raise RuntimeError("TestClient is single-use; construct a new client and application.")
        self._lifespan = asyncio.create_task(
            self.application({"type": "lifespan"}, self._incoming.get, self._outgoing.put)
        )
        await self._incoming.put({"type": "lifespan.startup"})
        try:
            response = await self._wait_lifespan()
            if response["type"] != "lifespan.startup.complete":
                raise RuntimeError(response.get("message", "Startup failed."))
        except BaseException:
            self._lifespan.cancel()
            await asyncio.gather(self._lifespan, return_exceptions=True)
            raise
        self._active = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._lifespan is None:
            raise RuntimeError("TestClient lifespan was not started.")
        self._active = False
        await self._incoming.put({"type": "lifespan.shutdown"})
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._wait_lifespan()
                await self._lifespan
            if response["type"] != "lifespan.shutdown.complete":
                raise RuntimeError(response.get("message", "Shutdown failed."))
        finally:
            if not self._lifespan.done():
                self._lifespan.cancel()
                await asyncio.gather(self._lifespan, return_exceptions=True)

    async def _wait_lifespan(self) -> Message:
        if self._lifespan is None:
            raise RuntimeError("TestClient lifespan was not started.")
        message = asyncio.create_task(self._outgoing.get())
        try:
            async with asyncio.timeout(self._timeout):
                await asyncio.wait((message, self._lifespan), return_when=asyncio.FIRST_COMPLETED)
                if self._lifespan.done() and not self._lifespan.cancelled():
                    error = self._lifespan.exception()
                    if error is not None:
                        raise error
                if message.done():
                    return message.result()
                if not self._outgoing.empty():
                    return self._outgoing.get_nowait()
                await self._lifespan
                raise RuntimeError("Lifespan ended without a protocol acknowledgement.")
        finally:
            if not message.done():
                message.cancel()
            await asyncio.gather(message, return_exceptions=True)

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes = b"",
        headers: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
    ) -> TestResponse:
        """Send a complete buffered HTTP request and verify response framing."""
        if not self._active:
            raise RuntimeError("Send requests inside the TestClient context manager.")
        if not isinstance(method, str) or not method:
            raise TypeError("Request method must be a nonempty string.")
        if not isinstance(path, str):
            raise TypeError("Request path must be a string.")
        if not isinstance(body, bytes):
            raise TypeError("Request body must be bytes.")
        url = urlsplit(path)
        pairs = (
            headers.items()
            if isinstance(headers, Mapping)
            else (headers if headers is not None else ())
        )
        wire_headers: list[tuple[bytes, bytes]] = []
        for name, value in pairs:
            if not isinstance(name, str) or not isinstance(value, str):
                raise TypeError("Request header names and values must be strings.")
            try:
                wire_headers.append((name.encode("latin-1"), value.encode("latin-1")))
            except UnicodeEncodeError as exc:
                raise ValueError("Request headers must be Latin-1 encodable.") from exc
        messages: list[Message] = []
        received = False

        async def receive() -> Message:
            """Provide one complete request frame, then model client disconnect."""
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: Message) -> None:
            """Validate ASGI response ordering while collecting test-client frames."""
            if not messages:
                if message["type"] != "http.response.start":
                    raise AssertionError("Response body preceded response start.")
            else:
                if message["type"] != "http.response.body":
                    raise AssertionError("Unexpected response frame or duplicate response start.")
                if messages[-1]["type"] == "http.response.body" and not messages[-1].get(
                    "more_body", False
                ):
                    raise AssertionError("Response frame followed the final body.")
            messages.append(message)

        await self.application(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "method": method.upper(),
                "path": unquote(url.path or "/", encoding="utf-8", errors="strict"),
                "raw_path": (url.path or "/").encode("utf-8"),
                "scheme": url.scheme or "http",
                "http_version": "1.1",
                "root_path": "",
                "client": ("127.0.0.1", 54321),
                "query_string": url.query.encode(),
                "headers": wire_headers,
            },
            receive,
            send,
        )
        if not messages or messages[0]["type"] != "http.response.start":
            raise AssertionError("ASGI application did not start a response.")
        if messages[-1]["type"] != "http.response.body" or messages[-1].get("more_body", False):
            raise AssertionError("ASGI response stream did not finish.")
        return TestResponse(
            messages[0]["status"],
            Headers([(k.decode(), v.decode()) for k, v in messages[0]["headers"]]),
            b"".join(m.get("body", b"") for m in messages[1:]),
        )


__all__ = ["TestClient", "TestResponse"]
