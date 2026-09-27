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
"""Provider-neutral HTTP rate-limiting middleware."""

from __future__ import annotations

from collections.abc import Callable

from orbit.asgi.middleware import NextHandler
from orbit.asgi.request import Headers, Request
from orbit.asgi.response import Response
from orbit.security.ratelimit import RateLimiter

RateLimitKey = Callable[[Request], str]


class RateLimitMiddleware:
    """Apply a bounded token bucket to requests and emit standard limit headers.

    The default key is the validated client host. Deployments behind a proxy should enable
    trusted forwarded headers on the application before using it; custom key functions can
    instead select an authenticated identity or another bounded tenant key.
    """

    def __init__(
        self,
        limit: int,
        period: float,
        *,
        key: RateLimitKey | None = None,
        max_keys: int = 10_000,
    ) -> None:
        if key is not None and not callable(key):
            raise TypeError("Rate-limit key functions must be callable.")
        self._limiter = RateLimiter(limit, period, max_keys=max_keys)
        self._key = key if key is not None else (lambda request: request.client_host or "anonymous")

    @property
    def limiter(self) -> RateLimiter:
        """Expose the underlying bounded limiter for operational inspection and reset."""
        return self._limiter

    async def __call__(self, request: Request, next_handler: NextHandler) -> Response:
        """Consume one token, short-circuiting rejected requests before route dispatch."""
        key = self._key(request)
        if not isinstance(key, str):
            raise TypeError("Rate-limit key functions must return strings.")
        decision = self._limiter.check(key)
        if not decision.allowed:
            response = Response.json(
                {"code": "security.rate-limited", "retry_after": decision.retry_after},
                status=429,
            )
            return self._with_headers(
                response,
                remaining=decision.remaining,
                retry_after=max(1, int(decision.retry_after + 0.999)),
            )
        response = await next_handler(request)
        return self._with_headers(response, remaining=decision.remaining)

    def _with_headers(
        self, response: Response, *, remaining: int, retry_after: int | None = None
    ) -> Response:
        if not isinstance(response.headers, Headers):
            raise TypeError("Responses must expose validated headers.")
        headers = [
            (name, value)
            for name, value in response.headers.pairs
            if name not in {"x-ratelimit-limit", "x-ratelimit-remaining", "retry-after"}
        ]
        headers.extend(
            [
                ("x-ratelimit-limit", str(self._limiter.limit)),
                ("x-ratelimit-remaining", str(max(0, remaining))),
            ]
        )
        if retry_after is not None:
            headers.append(("retry-after", str(retry_after)))
        return Response(response.status, response.body, headers, response.stream)


__all__ = ["RateLimitKey", "RateLimitMiddleware"]
