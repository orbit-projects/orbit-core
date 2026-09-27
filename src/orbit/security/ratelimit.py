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
"""Bounded in-process token-bucket rate limiting for Core surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from time import monotonic

from orbit._limits import is_finite_number

_MAX_LIMIT = 10_000_000
_MAX_PERIOD = 31_536_000.0
_MAX_KEYS = 1_000_000


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


@dataclass(frozen=True)
class RateLimitResult:
    """Decision and retry delay returned by a limiter."""

    allowed: bool
    remaining: int
    retry_after: float

    def __post_init__(self) -> None:
        """Validate the immutable decision before it reaches HTTP or admin responses."""
        if not isinstance(self.allowed, bool):
            raise TypeError("Rate-limit decisions must use a boolean allowed value.")
        if (
            isinstance(self.remaining, bool)
            or not isinstance(self.remaining, int)
            or self.remaining < 0
        ):
            raise ValueError("Rate-limit remaining tokens must be nonnegative integers.")
        if (
            isinstance(self.retry_after, bool)
            or not isinstance(self.retry_after, (int, float))
            or not is_finite_number(self.retry_after)
            or self.retry_after < 0
        ):
            raise ValueError("Rate-limit retry delays must be finite and nonnegative.")
        object.__setattr__(self, "retry_after", float(self.retry_after))


class RateLimiter:
    """Token-bucket limiter with bounded arithmetic and identity cardinality.

    Capacity, refill period and retained-key count are deliberately bounded. This keeps the
    in-process implementation predictable under configuration mistakes and prevents extreme
    values from overflowing the floating-point token accounting path.
    """

    def __init__(self, limit: int, period: float, *, max_keys: int = 10_000) -> None:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= _MAX_LIMIT
            or isinstance(period, bool)
            or not isinstance(period, (int, float))
            or not is_finite_number(period)
            or not 0 < period <= _MAX_PERIOD
            or isinstance(max_keys, bool)
            or not isinstance(max_keys, int)
            or not 1 <= max_keys <= _MAX_KEYS
        ):
            raise ValueError("Rate limiter values are invalid.")
        rate = limit / period
        if not is_finite_number(rate) or rate <= 0:
            raise ValueError("Rate limiter refill arithmetic is outside safe bounds.")
        self._limit = limit
        self._period = period
        self._rate = rate
        self._max_keys = max_keys
        self._buckets: dict[str, _Bucket] = {}
        self._lock = RLock()

    @property
    def limit(self) -> int:
        """Return the configured token capacity without exposing mutable policy state."""
        return self._limit

    @property
    def period(self) -> float:
        """Return the configured refill period without exposing mutable policy state."""
        return self._period

    def check(self, key: str, *, cost: int = 1, now: float | None = None) -> RateLimitResult:
        """Consume tokens for a key and return an explicit retry delay when rejected."""
        self._validate_key(key)
        if isinstance(cost, bool) or not isinstance(cost, int) or cost < 1 or cost > self.limit:
            raise ValueError("Rate-limit cost must be within the configured limit.")
        current = monotonic() if now is None else now
        if (
            isinstance(current, bool)
            or not isinstance(current, (int, float))
            or not is_finite_number(current)
        ):
            raise ValueError("Rate-limit time must be finite.")
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self._max_keys:
                    self._evict_oldest()
                bucket = _Bucket(float(self.limit), current)
                self._buckets[key] = bucket
            else:
                # A caller-supplied clock can move backwards (for example after a
                # process restore). Never rewind bucket time, which would over-credit
                # tokens on the next forward sample.
                elapsed = max(0.0, current - bucket.updated_at)
                bucket.tokens = min(
                    float(self.limit),
                    bucket.tokens + elapsed * self._rate,
                )
                bucket.updated_at = max(bucket.updated_at, current)
            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return RateLimitResult(True, int(bucket.tokens), 0.0)
            retry_after = (cost - bucket.tokens) / self._rate
            return RateLimitResult(False, int(bucket.tokens), retry_after)

    def reset(self, key: str) -> bool:
        """Remove one key's bucket and report whether it existed."""
        self._validate_key(key)
        with self._lock:
            return self._buckets.pop(key, None) is not None

    @property
    def key_count(self) -> int:
        """Return the number of active keys."""
        with self._lock:
            return len(self._buckets)

    def _evict_oldest(self) -> None:
        oldest = min(self._buckets, key=lambda key: self._buckets[key].updated_at)
        del self._buckets[oldest]

    @staticmethod
    def _validate_key(key: str) -> None:
        """Validate the bounded, control-character-free identity key contract."""
        if (
            not isinstance(key, str)
            or not key
            or len(key) > 255
            or any(ord(character) < 0x20 or ord(character) == 0x7F for character in key)
        ):
            raise ValueError("Rate-limit keys must be nonempty and at most 255 characters.")


__all__ = ["RateLimitResult", "RateLimiter"]
