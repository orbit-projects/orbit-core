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
"""Typed event delivery with unsubscribe, bounded diagnostics and failure aggregation."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
from collections import deque
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar
from uuid import UUID

from orbit._limits import _MAX_CORE_CAPACITY, is_finite_number
from orbit.events.models import Event
from orbit.events.store import EventStore, StoredEvent
from orbit.types import EventId, SubscriptionId, new_subscription_id

T = TypeVar("T")
_LOG = logging.getLogger(__name__)
_EVENT_NAME_PATTERN = re.compile(r"[a-z][a-z0-9.-]{0,126}")
DeadLetter = Callable[[Event[Any], Exception], Awaitable[None] | None]
EventFilter = Callable[[Event[Any]], bool]
_current_subscription: ContextVar[SubscriptionId | None] = ContextVar(
    "orbit_current_subscription", default=None
)


def _validate_event_name(name: str) -> None:
    """Apply the event-name contract to public delivery records and subscriptions."""
    if not isinstance(name, str) or _EVENT_NAME_PATTERN.fullmatch(name) is None:
        raise ValueError("Event names must be lowercase identifier-shaped strings.")


class FailurePolicy(StrEnum):
    """Subscriber failure handling policy."""

    RAISE = "raise"
    DEAD_LETTER = "dead-letter"


@dataclass(frozen=True)
class Subscription:
    """Opaque, validated handle used to remove one event registration."""

    id: SubscriptionId
    name: str
    max_retries: int = 0
    priority: int = 50

    def __post_init__(self) -> None:
        """Validate handle identity and retry policy before it crosses the bus boundary."""
        if not isinstance(self.id, UUID):
            raise TypeError("Subscription IDs must be UUID-backed identifiers.")
        _validate_event_name(self.name)
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or not 0 <= self.max_retries <= _MAX_CORE_CAPACITY
        ):
            raise ValueError("Subscription retry counts must be nonnegative integers.")
        if (
            isinstance(self.priority, bool)
            or not isinstance(self.priority, int)
            or not 0 <= self.priority <= 100
        ):
            raise ValueError("Subscription priorities must be integers from 0 through 100.")


@dataclass(frozen=True)
class Delivery:
    """Validated payload-free delivery record; history never retains event secrets."""

    event_id: EventId
    name: str
    subscriber_count: int
    failures: int
    deduplicated: bool = False

    def __post_init__(self) -> None:
        """Validate diagnostic identity and counters before publication to observers."""
        if not isinstance(self.event_id, UUID):
            raise TypeError("Delivery event IDs must be UUID-backed identifiers.")
        _validate_event_name(self.name)
        for value, label in (
            (self.subscriber_count, "subscriber counts"),
            (self.failures, "delivery failure counts"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label.capitalize()} must be nonnegative integers.")
        if self.failures > self.subscriber_count:
            raise ValueError("Delivery failures cannot exceed subscriber counts.")
        if not isinstance(self.deduplicated, bool):
            raise TypeError("Delivery deduplicated must be a boolean.")


class EventBus:
    """Deliver to a priority-ordered subscriber snapshot within a concurrency budget.

    Handler failures are aggregated after delivery. Cancellation propagates immediately.
    Cancellation-resistant subscriber and dead-letter callbacks are detached at the bus deadline
    and tracked per subscription, so repeated publication cannot accumulate orphan tasks. This is
    an in-process bus: no persistence, retries or distributed delivery guarantees.
    """

    def __init__(
        self,
        *,
        timeout: float = 30,
        history_size: int = 100,
        max_concurrency: int = 1,
        max_subscribers: int = 10_000,
        store: EventStore | None = None,
    ) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not is_finite_number(timeout)
            or timeout <= 0
            or isinstance(history_size, bool)
            or not isinstance(history_size, int)
            or not 0 <= history_size <= _MAX_CORE_CAPACITY
            or isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or not 1 <= max_concurrency <= _MAX_CORE_CAPACITY
            or isinstance(max_subscribers, bool)
            or not isinstance(max_subscribers, int)
            or not 1 <= max_subscribers <= _MAX_CORE_CAPACITY
        ):
            raise ValueError("Event bus limits are invalid.")
        self._subscribers: dict[
            SubscriptionId,
            tuple[
                str,
                Callable[[Event[Any]], Any],
                type[Any] | None,
                int,
                int,
                float,
                float,
                FailurePolicy,
                DeadLetter | None,
                EventFilter | None,
                SubscriptionId,
            ],
        ] = {}
        # One lock per subscription prevents concurrent publications from starting a second
        # callback while the first callback is still being detached after a deadline. The
        # delivery semaphore remains the bus-wide limit; this lock only serializes one handler.
        self._subscription_locks: dict[SubscriptionId, asyncio.Lock] = {}
        self._history: deque[Delivery] = deque(maxlen=history_size)
        self._timeout = timeout
        self._max_concurrency = max_concurrency
        self._delivery_semaphore = asyncio.Semaphore(max_concurrency)
        self._max_subscribers = max_subscribers
        self._store = store
        self._closed = False
        self._delivery_keys: deque[tuple[str, str]] = deque(maxlen=history_size)
        self._delivery_key_set: set[tuple[str, str]] = set()
        self._delivery_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self._state_lock = asyncio.Lock()
        self._active_publishes = 0
        self._publish_done = asyncio.Event()
        self._publish_done.set()
        self._closing = False
        self._store_closed = False
        self._store_close_task: asyncio.Task[None] | None = None
        self._store_operation_task: asyncio.Task[None] | None = None
        self._detached_callbacks: dict[tuple[str, SubscriptionId], asyncio.Future[Any]] = {}
        self._published = 0
        self._failed = 0
        self._deduplicated = 0

    @property
    def stats(self) -> tuple[int, int, int]:
        """Return cumulative published, failed-delivery and deduplicated counts."""
        return self._published, self._failed, self._deduplicated

    @property
    def history(self) -> tuple[Delivery, ...]:
        """Return bounded delivery metadata without event payloads."""
        return tuple(self._history)

    @property
    def closed(self) -> bool:
        """Whether this bus has been closed and will reject new deliveries."""
        return self._closed

    def subscribe(
        self,
        event_name: str,
        subscriber: Callable[[Event[T]], Awaitable[None] | None],
        *,
        payload_type: type[T] | None = None,
        max_retries: int = 0,
        priority: int = 50,
        backoff_initial: float = 0.0,
        backoff_multiplier: float = 2.0,
        failure_policy: FailurePolicy = FailurePolicy.RAISE,
        dead_letter: DeadLetter | None = None,
        predicate: EventFilter | None = None,
    ) -> Subscription:
        """Register a handler with an optional runtime payload contract."""
        if self._closed:
            raise RuntimeError("Event bus is closed.")
        if len(self._subscribers) >= self._max_subscribers:
            raise RuntimeError("Event subscriber capacity reached.")
        if not callable(subscriber):
            raise TypeError("Event subscriber must be callable.")
        if predicate is not None and not callable(predicate):
            raise TypeError("Event predicate must be callable.")
        if dead_letter is not None and not callable(dead_letter):
            raise TypeError("Dead-letter handler must be callable.")
        if payload_type is not None and (payload_type is Any or not isinstance(payload_type, type)):
            raise TypeError("payload_type must be a runtime-checkable type.")
        Event(name=event_name, payload=None)
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or not 0 <= max_retries <= _MAX_CORE_CAPACITY
            or isinstance(priority, bool)
            or not isinstance(priority, int)
            or not 0 <= priority <= 100
            or isinstance(backoff_initial, bool)
            or not isinstance(backoff_initial, (int, float))
            or not is_finite_number(backoff_initial)
            or backoff_initial < 0
            or isinstance(backoff_multiplier, bool)
            or not isinstance(backoff_multiplier, (int, float))
            or not is_finite_number(backoff_multiplier)
            or backoff_multiplier < 1
        ):
            raise ValueError("Retry limits and backoff values are invalid.")
        if not isinstance(failure_policy, FailurePolicy):
            raise TypeError("failure_policy must be a FailurePolicy.")
        if failure_policy is FailurePolicy.DEAD_LETTER and dead_letter is None:
            raise ValueError("DEAD_LETTER requires a dead_letter callback.")
        handle = Subscription(new_subscription_id(), event_name, max_retries, priority)
        self._subscribers[handle.id] = (
            event_name,
            subscriber,
            payload_type,
            priority,
            max_retries,
            backoff_initial,
            backoff_multiplier,
            failure_policy,
            dead_letter,
            predicate,
            handle.id,
        )
        self._subscription_locks[handle.id] = asyncio.Lock()
        return handle

    def unsubscribe(self, subscription: Subscription) -> bool:
        """Remove a registration; return whether it existed."""
        if not isinstance(subscription, Subscription):
            raise TypeError("subscription must be a Subscription instance.")
        removed = self._subscribers.pop(subscription.id, None) is not None
        self._cancel_subscription_callbacks(subscription.id)
        if self._active_publishes == 0:
            self._cleanup_subscription_locks()
        return removed

    async def publish(self, event: Event[Any]) -> None:
        """Deliver one event while keeping durable storage alive through completion."""
        if not isinstance(event, Event):
            raise TypeError("event must be an Event instance.")
        async with self._state_lock:
            if self._closed or self._closing:
                raise RuntimeError("Event bus is closed.")
            self._active_publishes += 1
            self._publish_done.clear()
        try:
            await self._publish(event)
        finally:
            async with self._state_lock:
                self._active_publishes -= 1
                if self._active_publishes == 0:
                    self._cleanup_subscription_locks()
                    if not self._detached_callbacks:
                        self._publish_done.set()

    async def _publish(self, event: Event[Any]) -> None:
        """Deliver detached event copies and aggregate failures after all subscribers run."""
        if event.delivery_key is not None:
            key = (event.name, event.delivery_key)
            async with self._delivery_lock:
                if key in self._delivery_key_set:
                    self._published += 1
                    self._deduplicated += 1
                    self._history.append(Delivery(event.id, event.name, 0, 0, True))
                    return
                if self._store is not None:
                    await self._store.append(event.model_copy(deep=True))
                self._remember_delivery_key(key)
        elif self._store is not None:
            await self._store.append(event.model_copy(deep=True))
        subscribers = sorted(
            (item for item in self._subscribers.values() if item[0] == event.name),
            key=lambda item: -item[3],
        )

        async def deliver(item: tuple[Any, ...]) -> Exception | None:
            """Apply filtering, payload validation, retry, and failure policy to one subscriber."""
            (
                _,
                callback,
                payload_type,
                _,
                retries,
                delay,
                multiplier,
                policy,
                dead_letter,
                predicate,
                subscription_id,
            ) = item
            if predicate is not None:
                try:
                    decision = predicate(event.model_copy(deep=True))
                    if not isinstance(decision, bool):
                        raise TypeError("Event predicates must return bool.")
                    if not decision:
                        return None
                except Exception as exc:
                    return exc
            error: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    callback_key = ("subscriber", subscription_id)
                    self._ensure_callback_available(callback_key)
                    if payload_type is not None and not isinstance(event.payload, payload_type):
                        raise TypeError(f"Event {event.name} requires {payload_type}.")
                    result = callback(event.model_copy(deep=True))
                    if inspect.isawaitable(result):
                        await self._await_callback(result, callback_key)
                    error = None
                    break
                except Exception as exc:
                    error = exc
                    if attempt < retries and delay:
                        try:
                            retry_delay = delay * multiplier**attempt
                        except OverflowError:
                            retry_delay = self._timeout
                        await asyncio.sleep(min(self._timeout, retry_delay))
            if error is not None:
                if policy is FailurePolicy.DEAD_LETTER and dead_letter is not None:
                    try:
                        outcome = dead_letter(event.model_copy(deep=True), error)
                        if inspect.isawaitable(outcome):
                            await self._await_callback(outcome, ("dead-letter", subscription_id))
                    except Exception:
                        return RuntimeError("Dead-letter delivery failed.")
                else:
                    return error
            return None

        async def bounded(item: tuple[Any, ...]) -> Exception | None:
            """Deliver one subscriber under both per-handler and bus-wide limits."""
            subscription_id = item[-1]
            lock = self._subscription_locks[subscription_id]
            if _current_subscription.get() == subscription_id:
                # A handler may intentionally publish another event to itself. The nested
                # publication is already within this subscription's execution context, so
                # waiting for the non-reentrant lock or acquiring the same semaphore twice
                # would deadlock the handler.
                return await deliver(item)
            token = _current_subscription.set(subscription_id)
            try:
                async with lock, self._delivery_semaphore:
                    return await deliver(item)
            finally:
                _current_subscription.reset(token)

        outcomes = await asyncio.gather(*(bounded(item) for item in subscribers))
        failures = [outcome for outcome in outcomes if outcome is not None]
        self._published += 1
        self._failed += len(failures)
        self._history.append(Delivery(event.id, event.name, len(subscribers), len(failures)))
        if failures:
            raise ExceptionGroup(f"Event {event.name} delivery failed", failures)

    def _remember_delivery_key(self, key: tuple[str, str]) -> None:
        if self._delivery_keys.maxlen:
            if len(self._delivery_keys) == self._delivery_keys.maxlen:
                self._delivery_key_set.discard(self._delivery_keys.popleft())
            self._delivery_keys.append(key)
            self._delivery_key_set.add(key)

    def _cleanup_subscription_locks(self) -> None:
        """Release lock objects for registrations removed after all snapshots have drained."""
        for subscription_id, lock in tuple(self._subscription_locks.items()):
            if subscription_id not in self._subscribers and not lock.locked():
                self._subscription_locks.pop(subscription_id, None)

    def _ensure_callback_available(self, key: tuple[str, SubscriptionId]) -> None:
        """Reject a callback invocation while an earlier timed-out call is still cancelling."""
        task = self._detached_callbacks.get(key)
        if task is None:
            return
        if task.done():
            self._retire_callback(key, task)
            return
        raise RuntimeError("Event callback is still cancelling.")

    async def _await_callback(
        self, outcome: Awaitable[Any], key: tuple[str, SubscriptionId]
    ) -> None:
        """Await one callback under a deadline without waiting for cancellation-resistant code."""
        self._ensure_callback_available(key)
        task = asyncio.ensure_future(outcome)
        try:
            done, _ = await asyncio.wait({task}, timeout=self._timeout)
            if not done:
                self._detach_callback(key, task)
                raise TimeoutError("Event callback exceeded its deadline.")
            task.result()
        except asyncio.CancelledError:
            self._detach_callback(key, task)
            raise

    def _detach_callback(self, key: tuple[str, SubscriptionId], task: asyncio.Future[Any]) -> None:
        """Cancel and retain one callback until it exits, consuming its eventual result."""
        self._publish_done.clear()
        self._detached_callbacks[key] = task
        task.cancel()
        task.add_done_callback(lambda finished: self._retire_callback(key, finished))

    def _retire_callback(self, key: tuple[str, SubscriptionId], task: asyncio.Future[Any]) -> None:
        """Forget a detached callback and consume any late exception without warnings."""
        if self._detached_callbacks.get(key) is task:
            del self._detached_callbacks[key]
        _consume_task_result(task)
        if self._active_publishes == 0 and not self._detached_callbacks:
            self._publish_done.set()

    def _cancel_subscription_callbacks(self, subscription_id: SubscriptionId) -> None:
        """Request cancellation of detached callbacks owned by one subscription."""
        for (_kind, current_id), task in tuple(self._detached_callbacks.items()):
            if current_id == subscription_id:
                task.cancel()

    def _cancel_detached_callbacks(self) -> None:
        """Request cancellation of all callbacks that outlived their delivery deadline."""
        for task in tuple(self._detached_callbacks.values()):
            task.cancel()

    async def replay(
        self, *, after: int = 0, event_name: str | None = None, limit: int = 100
    ) -> tuple[StoredEvent, ...]:
        """Read persisted events through the configured store without redelivering them."""
        if (
            isinstance(after, bool)
            or not isinstance(after, int)
            or after < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 10_000
        ):
            raise ValueError("after must be nonnegative and limit must be between 1 and 10000.")
        if event_name is not None and (
            not isinstance(event_name, str)
            or not re.fullmatch(r"[a-z][a-z0-9.-]{0,126}", event_name)
        ):
            raise ValueError("event_name must be a valid event name.")
        if self._store is None:
            raise RuntimeError("No event store is configured.")
        return await self._store.read(after=after, event_name=event_name, limit=limit)

    def close(self) -> None:
        """Release subscriber references; retain only bounded delivery diagnostics."""
        self._closed = True
        self._closing = True
        self._subscribers.clear()
        if self._active_publishes == 0:
            self._cleanup_subscription_locks()
        self._cancel_detached_callbacks()

    async def aclose(self) -> None:
        """Close the bus and await an optionally configured durable store.

        The close operation is shared and shielded from caller cancellation. A cancelled
        caller still receives ``CancelledError`` after the bus finishes its bounded cleanup,
        so durable storage is not left open merely because an owner task was interrupted.
        """
        if self._close_task is None or (
            self._close_task.done() and self._store is not None and not self._store_closed
        ):
            self._close_task = asyncio.create_task(self._aclose())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _aclose(self) -> None:
        """Perform the shared event-bus close operation under the close lock."""
        async with self._close_lock:
            async with self._state_lock:
                self._closing = True
                self._closed = True
                self._subscribers.clear()
            self._cancel_detached_callbacks()
            try:
                await asyncio.wait_for(asyncio.shield(self._publish_done.wait()), self._timeout)
            except TimeoutError:
                _LOG.error("Event publishes did not terminate before shutdown deadline")
            if self._store is not None and not self._store_closed:
                if self._active_publishes == 0 and not self._detached_callbacks:
                    await self._close_store()
                elif self._store_close_task is None or self._store_close_task.done():
                    self._store_close_task = asyncio.create_task(self._close_store_when_idle())

    async def _close_store_when_idle(self) -> None:
        """Close durable storage after in-flight publishes release their ownership."""
        try:
            await asyncio.wait_for(self._publish_done.wait(), self._timeout)
        except TimeoutError:
            # Keep the deferred waiter bounded as well as the public close call. A later
            # ``aclose`` can retry once the in-flight publish has released the store safely.
            _LOG.error("Deferred event-store cleanup waited for publishes past its deadline")
            return
        if self._store is not None and not self._store_closed:
            try:
                await self._close_store()
            except Exception:
                _LOG.exception("Event store close failed after deferred shutdown")

    async def _close_store(self) -> None:
        """Attempt store cleanup within the bus deadline without cancelling the store task."""
        if self._store is None or self._store_closed:
            return
        if self._store_operation_task is None:
            self._store_operation_task = asyncio.create_task(
                self._store.close(), name="orbit:event-store-close"
            )
            self._store_operation_task.add_done_callback(self._record_store_close)
        try:
            await asyncio.wait_for(
                asyncio.shield(self._store_operation_task), timeout=self._timeout
            )
        except TimeoutError:
            _LOG.error("Event store close exceeded its deadline")
        except asyncio.CancelledError:
            # The detached operation remains owned by the bus and is consumed by its callback.
            raise
        else:
            self._store_closed = True

    def _record_store_close(self, task: asyncio.Task[None]) -> None:
        """Record a detached store-close outcome without loop-level exception warnings."""
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            _LOG.exception("Event store close failed")
        else:
            self._store_closed = True


def _consume_task_result(task: asyncio.Future[Any]) -> None:
    """Consume a detached callback result without emitting loop-level warnings."""
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        return


__all__ = ["Delivery", "EventBus", "EventFilter", "FailurePolicy", "Subscription"]
