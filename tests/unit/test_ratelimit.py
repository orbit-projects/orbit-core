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
"""Verify token-bucket accounting, clock handling, and limiter cardinality bounds."""

import pytest

from orbit.asgi import RateLimitMiddleware, Response
from orbit.asgi.request import Request
from orbit.security import RateLimiter, RateLimitResult


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RateLimitResult(1, 1, 0),
        lambda: RateLimitResult(True, -1, 0),
        lambda: RateLimitResult(True, 1, float("nan")),
        lambda: RateLimitResult(True, 1, -1),
    ],
)
def test_rate_limit_results_reject_invalid_public_values(factory) -> None:
    """Rate-limit decisions remain safe when constructed outside the limiter."""
    with pytest.raises((TypeError, ValueError)):
        factory()


def test_token_bucket_refills_and_reports_retry_after():
    limiter = RateLimiter(2, 10)
    with pytest.raises(AttributeError):
        limiter.limit = 100  # type: ignore[misc]
    with pytest.raises(AttributeError):
        limiter.period = 1  # type: ignore[misc]
    assert limiter.check("user", now=0).allowed
    assert limiter.check("user", now=0).allowed
    rejected = limiter.check("user", now=0)
    assert not rejected.allowed
    assert rejected.retry_after == pytest.approx(5)
    assert limiter.check("user", now=5).allowed


def test_rate_limiter_does_not_rewind_bucket_on_clock_rollback():
    limiter = RateLimiter(1, 10)
    assert limiter.check("user", now=100).allowed
    assert not limiter.check("user", now=90).allowed
    assert not limiter.check("user", now=90).allowed
    assert limiter.check("user", now=110).allowed


def test_rate_limiter_bounds_keys_and_supports_reset():
    limiter = RateLimiter(1, 1, max_keys=1)
    limiter.check("first", now=0)
    limiter.check("second", now=1)
    assert limiter.key_count == 1
    assert limiter.reset("second")
    assert not limiter.reset("second")
    with pytest.raises(ValueError):
        limiter.check("", now=1)
    with pytest.raises(ValueError, match="finite"):
        limiter.check("third", now=float("nan"))
    with pytest.raises(ValueError, match="finite"):
        limiter.check("third", now=float("inf"))
    with pytest.raises(ValueError):
        limiter.check(None, now=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        limiter.check("bad\nkey", now=1)
    with pytest.raises(ValueError):
        limiter.check("user", cost=True)
    with pytest.raises(ValueError):
        limiter.reset(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        limiter.reset("bad\nkey")
    with pytest.raises(ValueError, match="finite"):
        limiter.check("user", now=True)


@pytest.mark.parametrize("period", [0, -1, True, float("inf"), float("nan")])
def test_rate_limiter_rejects_invalid_period(period: float) -> None:
    with pytest.raises(ValueError):
        RateLimiter(1, period)


def test_rate_limiter_rejects_unbounded_or_overflowing_policies() -> None:
    with pytest.raises(ValueError):
        RateLimiter(10_000_001, 1)
    with pytest.raises(ValueError):
        RateLimiter(1, 1e-320)
    with pytest.raises(ValueError):
        RateLimiter(1, 1, max_keys=1_000_001)


@pytest.mark.parametrize(
    "arguments",
    [(True, 1), ("1", 1), (1, 1, True), (1, "1", 1), (1, 1, "1")],
)
def test_rate_limiter_rejects_boolean_capacity_limits(arguments) -> None:
    with pytest.raises(ValueError):
        if len(arguments) == 2:
            RateLimiter(*arguments)
        else:
            RateLimiter(arguments[0], arguments[1], max_keys=arguments[2])


@pytest.mark.asyncio
async def test_rate_limit_middleware_validates_and_preserves_key_provider() -> None:
    with pytest.raises(TypeError, match="callable"):
        RateLimitMiddleware(1, 1, key="client")  # type: ignore[arg-type]

    class FalseyKey:
        def __bool__(self) -> bool:
            return False

        def __call__(self, request: Request) -> str:
            return "custom"

    key = FalseyKey()
    middleware = RateLimitMiddleware(1, 1, key=key)

    async def next_handler(request: Request) -> Response:
        return Response.text("ok")

    await middleware(Request("GET", "/"), next_handler)
    assert middleware.limiter.key_count == 1
