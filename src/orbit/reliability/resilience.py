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
"""Cancellation-safe retry, circuit-breaker and concurrency-isolation primitives."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from time import monotonic
from typing import Protocol, TypeVar, runtime_checkable

from orbit._limits import _MAX_CORE_CAPACITY, is_finite_number

T = TypeVar("T")
Operation = Callable[[], T | Awaitable[T]]


class FailureKind(StrEnum):
    """Classification used to decide whether an operation may be retried."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    RESOURCE = "resource"
    UNKNOWN = "unknown"


@runtime_checkable
class FailureClassifier(Protocol):
    """Adapter boundary for classifying failures without coupling Core to exception types."""

    def classify(self, error: Exception) -> FailureKind:
        """Return the operational category of an exception."""


class DefaultFailureClassifier:
    """Conservative defaults for common transport and programming failures."""

    def classify(self, error: Exception) -> FailureKind:
        """Classify common failures without treating unknown exceptions as retryable."""
        if isinstance(error, (TimeoutError, ConnectionError, OSError)):
            return FailureKind.TRANSIENT
        if isinstance(error, MemoryError):
            return FailureKind.RESOURCE
        if isinstance(error, (ValueError, TypeError, KeyError)):
            return FailureKind.PERMANENT
        return FailureKind.UNKNOWN


@dataclass(frozen=True)
class Deadline:
    """Monotonic deadline that composes parent and child timeout budgets."""

    timeout: float
    started_at: float = field(default_factory=monotonic)

    def __post_init__(self) -> None:
        if (
            isinstance(self.timeout, bool)
            or not isinstance(self.timeout, (int, float))
            or not is_finite_number(self.timeout)
            or self.timeout <= 0
            or isinstance(self.started_at, bool)
            or not isinstance(self.started_at, (int, float))
            or not is_finite_number(self.started_at)
        ):
            raise ValueError("Deadline timeout and start time must be finite and valid.")

    @property
    def remaining(self) -> float:
        """Return seconds remaining, clamped to zero after expiry."""
        return max(0.0, self.timeout - (monotonic() - self.started_at))

    @property
    def expired(self) -> bool:
        """Return whether no budget remains."""
        return self.remaining <= 0

    def child(self, timeout: float) -> Deadline:
        """Create a deadline bounded by both this deadline and ``timeout``."""
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not is_finite_number(timeout)
            or timeout <= 0
        ):
            raise ValueError("Child deadline timeout must be positive.")
        if self.expired:
            raise TimeoutError("Parent deadline has expired.")
        return Deadline(min(timeout, self.remaining))

    @asynccontextmanager
    async def scope(self) -> AsyncIterator[None]:
        """Bound an async operation by the remaining budget."""
        async with asyncio.timeout(self.remaining):
            yield


@dataclass(frozen=True)
class RetryPolicy:
    """Finite exponential retry schedule with an optional exception predicate."""

    max_attempts: int = 3
    initial_delay: float = 0.1
    multiplier: float = 2.0
    max_delay: float = 30.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or not 1 <= self.max_attempts <= _MAX_CORE_CAPACITY
            or isinstance(self.initial_delay, bool)
            or not isinstance(self.initial_delay, (int, float))
            or not is_finite_number(self.initial_delay)
            or self.initial_delay < 0
            or isinstance(self.multiplier, bool)
            or not isinstance(self.multiplier, (int, float))
            or not is_finite_number(self.multiplier)
            or self.multiplier < 1
            or isinstance(self.max_delay, bool)
            or not isinstance(self.max_delay, (int, float))
            or not is_finite_number(self.max_delay)
            or self.max_delay < 0
        ):
            raise ValueError("Retry policy values are invalid.")


def _retry_delay(policy: RetryPolicy, attempt: int) -> float:
    """Return a capped backoff without allowing exponentiation to escape its bounds."""
    if policy.initial_delay == 0 or policy.max_delay == 0:
        return 0.0
    if policy.initial_delay >= policy.max_delay:
        return float(policy.max_delay)
    try:
        delay = policy.initial_delay * policy.multiplier**attempt
    except OverflowError:
        return float(policy.max_delay)
    return (
        float(min(policy.max_delay, delay)) if is_finite_number(delay) else float(policy.max_delay)
    )


async def retry(
    operation: Operation[T],
    policy: RetryPolicy | None = None,
    *,
    retry_if: Callable[[Exception], bool] | None = None,
) -> T:
    """Run an operation with bounded backoff; cancellation always propagates immediately."""
    if not callable(operation):
        raise TypeError("operation must be callable.")
    if retry_if is not None and not callable(retry_if):
        raise TypeError("retry_if must be callable.")
    if policy is None:
        policy = RetryPolicy()
    elif not isinstance(policy, RetryPolicy):
        raise TypeError("policy must be a RetryPolicy.")
    for attempt in range(policy.max_attempts):
        try:
            result = operation()
            return await result if inspect.isawaitable(result) else result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if attempt + 1 >= policy.max_attempts or (retry_if is not None and not retry_if(exc)):
                raise
            delay = _retry_delay(policy, attempt)
            if delay:
                await asyncio.sleep(delay)
    raise AssertionError("unreachable")


async def resilient_call(
    operation: Operation[T],
    *,
    deadline: Deadline | None = None,
    retry_policy: RetryPolicy | None = None,
    circuit: CircuitBreaker | None = None,
    bulkhead: Bulkhead | None = None,
    classifier: FailureClassifier | None = None,
) -> T:
    """Compose deadline, retry, circuit and bulkhead policies with cancellation preserved."""
    if classifier is None:
        selected: FailureClassifier = DefaultFailureClassifier()
    elif not isinstance(classifier, FailureClassifier) or not callable(classifier.classify):
        raise TypeError("classifier must provide a callable classify method.")
    else:
        selected = classifier

    async def protected() -> T:
        """Apply the configured circuit and bulkhead around the operation."""
        if circuit is not None and bulkhead is not None:
            return await circuit.call(lambda: bulkhead.call(operation))
        if circuit is not None:
            return await circuit.call(operation)
        if bulkhead is not None:
            return await bulkhead.call(operation)
        result = operation()
        return await result if inspect.isawaitable(result) else result

    async def execute() -> T:
        """Retry only failures classified as transient by the selected classifier."""
        return await retry(
            protected,
            retry_policy,
            retry_if=lambda error: selected.classify(error) is FailureKind.TRANSIENT,
        )

    if deadline is None:
        return await execute()
    async with deadline.scope():
        return await execute()


class CircuitState(StrEnum):
    """Circuit breaker state."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class CircuitOpenError(RuntimeError):
    """Raised when a circuit rejects work while open."""


class CircuitBreaker:
    """Protect an operation after consecutive failures with one half-open probe."""

    def __init__(self, *, failure_threshold: int = 5, recovery_timeout: float = 30.0) -> None:
        if (
            isinstance(failure_threshold, bool)
            or not isinstance(failure_threshold, int)
            or not 1 <= failure_threshold <= _MAX_CORE_CAPACITY
            or isinstance(recovery_timeout, bool)
            or not isinstance(recovery_timeout, (int, float))
            or not is_finite_number(recovery_timeout)
            or recovery_timeout <= 0
        ):
            raise ValueError("Circuit breaker values are invalid.")
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_active = False
        # Every transition to OPEN or HALF_OPEN advances the epoch. An operation
        # that began in an older epoch must not overwrite a newer breaker state
        # when it completes later.
        self._epoch = 0
        self._lock = asyncio.Lock()

    @property
    def failure_threshold(self) -> int:
        """Return the fixed consecutive-failure threshold."""
        return self._failure_threshold

    @property
    def recovery_timeout(self) -> float:
        """Return the fixed half-open recovery delay."""
        return self._recovery_timeout

    @property
    def state(self) -> CircuitState:
        """Return the current breaker state for diagnostics and health reporting."""
        return self._state

    @property
    def failures(self) -> int:
        """Return consecutive failures recorded since the last successful call."""
        return self._failures

    async def call(self, operation: Operation[T]) -> T:
        """Execute an operation or reject it while the circuit is open."""
        probe = False
        async with self._lock:
            if self._state is CircuitState.OPEN:
                if monotonic() - self._opened_at < self.recovery_timeout:
                    raise CircuitOpenError("Circuit breaker is open.")
                self._state = CircuitState.HALF_OPEN
                self._epoch += 1
            if self._state is CircuitState.HALF_OPEN:
                if self._probe_active:
                    raise CircuitOpenError("Circuit breaker is probing recovery.")
                self._probe_active = True
                probe = True
            epoch = self._epoch
        if probe:
            try:
                return await self._attempt(operation, epoch)
            finally:
                async with self._lock:
                    self._probe_active = False
        return await self._attempt(operation, epoch)

    async def _attempt(self, operation: Operation[T], epoch: int) -> T:
        try:
            result = operation()
            value = await result if inspect.isawaitable(result) else result
        except asyncio.CancelledError:
            raise
        except Exception:
            async with self._lock:
                # An older operation may finish after recovery has already
                # entered a new half-open epoch. It must not fail the current
                # probe or alter the newer failure count.
                if epoch != self._epoch:
                    raise
                self._failures += 1
                # A half-open probe must reopen on its first failure. Also avoid
                # refreshing OPEN's timestamp because of an older in-flight call.
                should_open = self._state is CircuitState.HALF_OPEN or (
                    self._state is CircuitState.CLOSED and self._failures >= self.failure_threshold
                )
                if should_open:
                    self._state = CircuitState.OPEN
                    self._opened_at = monotonic()
                    self._epoch += 1
            raise
        else:
            async with self._lock:
                # A completion from a previous epoch cannot close a breaker
                # that has since opened because of newer failures.
                if epoch == self._epoch and self._state is not CircuitState.OPEN:
                    self._failures = 0
                    self._state = CircuitState.CLOSED
            return value


class Bulkhead:
    """Bound concurrent operations and optionally reject waiters after a timeout."""

    def __init__(self, limit: int, *, timeout: float | None = None) -> None:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= _MAX_CORE_CAPACITY
            or (
                timeout is not None
                and (
                    isinstance(timeout, bool)
                    or not isinstance(timeout, (int, float))
                    or not is_finite_number(timeout)
                    or timeout < 0
                )
            )
        ):
            raise ValueError("Bulkhead values are invalid.")
        self._limit = limit
        self._timeout = timeout
        self._semaphore = asyncio.Semaphore(limit)

    @property
    def limit(self) -> int:
        """Return the fixed concurrency capacity."""
        return self._limit

    @property
    def timeout(self) -> float | None:
        """Return the fixed waiter timeout."""
        return self._timeout

    async def call(self, operation: Operation[T]) -> T:
        """Run one operation under the concurrency limit."""
        if self.timeout is None:
            await self._semaphore.acquire()
        else:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self.timeout)
        try:
            result = operation()
            return await result if inspect.isawaitable(result) else result
        finally:
            self._semaphore.release()


__all__ = [
    "Bulkhead",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "Deadline",
    "DefaultFailureClassifier",
    "FailureClassifier",
    "FailureKind",
    "RetryPolicy",
    "retry",
    "resilient_call",
]
