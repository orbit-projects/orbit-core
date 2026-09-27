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
"""Typed event delivery, snapshot semantics and bounded diagnostic retention."""

import asyncio
from datetime import datetime
from uuid import uuid4

import pytest

from orbit.events import Event, EventBus, EventStore, EventTransport, FailurePolicy


async def test_failure_does_not_skip_other_subscribers():
    calls = []
    bus = EventBus()

    def bad(event):
        raise ValueError("subscriber failed")

    bus.subscribe("test", bad)
    bus.subscribe("test", lambda e: calls.append(e.payload))
    with pytest.raises(ExceptionGroup):
        await bus.publish(Event(name="test", payload=42))
    assert calls == [42]
    assert bus.history[-1].failures == 1


async def test_unsubscribe_and_close():
    bus = EventBus()
    calls = []
    token = bus.subscribe("test", lambda event: calls.append(event))
    assert bus.unsubscribe(token)
    assert not bus.unsubscribe(token)
    await bus.publish(Event(name="test", payload=1))
    assert not calls
    bus.close()
    with pytest.raises(RuntimeError):
        await bus.publish(Event(name="test", payload=1))
    with pytest.raises(RuntimeError):
        bus.subscribe("test", lambda event: None)


async def test_delivery_retains_typed_event_identity_and_subscription_identity():
    bus = EventBus()
    token = bus.subscribe("test", lambda event: None)
    event = Event(name="test", payload=None)

    await bus.publish(event)

    assert token.id
    assert bus.history[-1].event_id == event.id


def test_event_bus_bounds_subscriber_registration():
    bus = EventBus(max_subscribers=1)
    bus.subscribe("test", lambda event: None)
    with pytest.raises(RuntimeError, match="capacity"):
        bus.subscribe("test", lambda event: None)
    with pytest.raises(ValueError):
        EventBus(max_subscribers=0)


def test_event_bus_rejects_nonfinite_delivery_limits():
    with pytest.raises(ValueError):
        EventBus(timeout=float("inf"))
    with pytest.raises(ValueError):
        EventBus().subscribe("test", lambda event: None, backoff_initial=float("nan"))
    with pytest.raises(ValueError):
        EventBus().subscribe("test", lambda event: None, backoff_multiplier=float("inf"))


@pytest.mark.parametrize(
    "factory",
    [
        lambda: EventBus(timeout=True),
        lambda: EventBus(timeout="1"),
        lambda: EventBus(history_size=True),
        lambda: EventBus(history_size="1"),
        lambda: EventBus(history_size=1_000_001),
        lambda: EventBus(max_concurrency=True),
        lambda: EventBus(max_concurrency="1"),
        lambda: EventBus(max_concurrency=1_000_001),
        lambda: EventBus(max_subscribers=True),
        lambda: EventBus(max_subscribers="1"),
        lambda: EventBus(max_subscribers=1_000_001),
        lambda: EventBus().subscribe("test", lambda event: None, max_retries=True),
        lambda: EventBus().subscribe("test", lambda event: None, max_retries="1"),
        lambda: EventBus().subscribe("test", lambda event: None, max_retries=1_000_001),
        lambda: EventBus().subscribe("test", lambda event: None, priority=True),
        lambda: EventBus().subscribe("test", lambda event: None, priority="1"),
        lambda: EventBus().subscribe("test", lambda event: None, backoff_initial=True),
        lambda: EventBus().subscribe("test", lambda event: None, backoff_initial="1"),
        lambda: EventBus().subscribe("test", lambda event: None, backoff_multiplier=True),
        lambda: EventBus().subscribe("test", lambda event: None, backoff_multiplier="1"),
    ],
)
def test_event_bus_rejects_boolean_numeric_limits(factory):
    with pytest.raises(ValueError):
        factory()


async def test_async_close_closes_store_once_under_concurrency():
    class Store:
        closes = 0

        async def append(self, event):
            return 1

        async def read(self, *, after=0, event_name=None, limit=100):
            return ()

        async def prune_before(self, sequence):
            return 0

        async def close(self):
            self.closes += 1

    store = Store()
    assert isinstance(store, EventStore)
    bus = EventBus(store=store)
    await asyncio.gather(bus.aclose(), bus.aclose(), bus.aclose())
    assert store.closes == 1


async def test_async_close_waits_for_inflight_persisted_publish():
    started = asyncio.Event()
    release = asyncio.Event()

    class Store:
        closed = False

        async def append(self, event):
            started.set()
            await release.wait()
            if self.closed:
                raise AssertionError("store closed before append completed")
            return 1

        async def read(self, *, after=0, event_name=None, limit=100):
            return ()

        async def prune_before(self, sequence):
            return 0

        async def close(self):
            self.closed = True

    store = Store()
    bus = EventBus(store=store)
    publishing = asyncio.create_task(bus.publish(Event(name="persisted", payload=None)))
    await started.wait()
    closing = asyncio.create_task(bus.aclose())
    await asyncio.sleep(0)
    assert not closing.done()
    release.set()
    await publishing
    await closing
    assert store.closed


async def test_cancelled_async_close_cleans_store_before_propagating_cancellation():
    """Cancelling one close caller cannot interrupt the bus-owned cleanup task."""
    started = asyncio.Event()
    release = asyncio.Event()

    class Store:
        closed = False

        async def append(self, event):
            started.set()
            await release.wait()
            return 1

        async def read(self, *, after=0, event_name=None, limit=100):
            return ()

        async def prune_before(self, sequence):
            return 0

        async def close(self):
            self.closed = True

    store = Store()
    bus = EventBus(store=store)
    publishing = asyncio.create_task(bus.publish(Event(name="persisted", payload=None)))
    await started.wait()
    closing = asyncio.create_task(bus.aclose())
    await asyncio.sleep(0)
    closing.cancel()
    release.set()
    await publishing
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert bus._close_task is not None  # noqa: SLF001 - join the bus-owned close task.
    await bus._close_task  # noqa: SLF001 - verify cancellation-safe cleanup ownership.
    assert store.closed


async def test_async_close_is_bounded_and_defers_store_close_for_inflight_publish():
    release = asyncio.Event()
    entered = asyncio.Event()

    class Store:
        closed = False

        async def append(self, event):
            return 1

        async def read(self, *, after=0, event_name=None, limit=100):
            return ()

        async def prune_before(self, sequence):
            return 0

        async def close(self):
            self.closed = True

    async def slow(event):
        entered.set()
        while not release.is_set():
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                continue

    store = Store()
    bus = EventBus(store=store, timeout=0.01)
    bus.subscribe("slow", slow)
    publishing = asyncio.create_task(bus.publish(Event(name="slow", payload=None)))
    await entered.wait()
    await asyncio.wait_for(bus.aclose(), timeout=0.1)
    assert not store.closed
    release.set()
    with pytest.raises(ExceptionGroup):
        await asyncio.wait_for(publishing, timeout=0.1)
    assert bus._store_close_task is not None  # noqa: SLF001 - join deferred cleanup.
    await asyncio.wait_for(bus._store_close_task, timeout=0.1)  # noqa: SLF001
    assert store.closed


async def test_async_close_does_not_leave_an_unbounded_deferred_store_waiter():
    release = asyncio.Event()
    entered = asyncio.Event()

    class Store:
        closed = False

        async def append(self, event):
            return 1

        async def read(self, *, after=0, event_name=None, limit=100):
            return ()

        async def prune_before(self, sequence):
            return 0

        async def close(self):
            self.closed = True

    async def stubborn(event):
        entered.set()
        while not release.is_set():
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                continue

    store = Store()
    bus = EventBus(store=store, timeout=0.01)
    bus.subscribe("stubborn", stubborn)
    publishing = asyncio.create_task(bus.publish(Event(name="stubborn", payload=None)))
    await entered.wait()

    await asyncio.wait_for(bus.aclose(), timeout=0.1)
    deferred = bus._store_close_task  # noqa: SLF001 - inspect test-owned cleanup.
    assert deferred is not None
    await asyncio.wait_for(deferred, timeout=0.1)
    assert not store.closed

    release.set()
    with pytest.raises(ExceptionGroup):
        await asyncio.wait_for(publishing, timeout=0.1)
    await asyncio.wait_for(bus.aclose(), timeout=0.1)
    assert store.closed


async def test_async_close_bounds_idle_store_cleanup_without_cancelling_store_operation():
    release = asyncio.Event()
    started = asyncio.Event()

    class Store:
        closed = False

        async def append(self, event):
            return 1

        async def read(self, *, after=0, event_name=None, limit=100):
            return ()

        async def prune_before(self, sequence):
            return 0

        async def close(self):
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    continue
            self.closed = True

    store = Store()
    bus = EventBus(store=store, timeout=0.01)
    await asyncio.wait_for(bus.aclose(), timeout=0.1)
    await asyncio.wait_for(started.wait(), timeout=0.1)
    assert not store.closed
    operation = bus._store_operation_task  # noqa: SLF001 - join test-owned cleanup.
    assert operation is not None and not operation.done()
    release.set()
    await asyncio.wait_for(operation, timeout=0.1)
    await asyncio.wait_for(bus.aclose(), timeout=0.1)
    assert store.closed
    assert bus._store_closed  # noqa: SLF001 - assert test-owned cleanup state.


async def test_delivery_payload_isolation_and_history_bound():
    bus = EventBus(history_size=2)
    values = []
    bus.subscribe("test", lambda event: event.payload.append("changed"))
    bus.subscribe("test", lambda event: values.append(event.payload))
    for i in range(3):
        await bus.publish(Event(name="test", payload=[i]))
    assert values == [[0], [1], [2]]
    assert len(bus.history) == 2
    assert not hasattr(bus.history[0], "payload")


async def test_concurrency_budget_is_shared_across_concurrent_publishes():
    entered = asyncio.Event()
    release = asyncio.Event()
    active = 0
    maximum = 0

    async def handler(event):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        entered.set()
        await release.wait()
        active -= 1

    bus = EventBus(max_concurrency=1)
    bus.subscribe("test", handler)
    first = asyncio.create_task(bus.publish(Event(name="test", payload=1)))
    await entered.wait()
    second = asyncio.create_task(bus.publish(Event(name="test", payload=2)))
    await asyncio.sleep(0)
    assert maximum == 1
    assert not second.done()
    release.set()
    await asyncio.gather(first, second)


async def test_typed_payload_mismatch_and_handler_deadline():
    import asyncio

    bus = EventBus(timeout=0.01)
    bus.subscribe("typed", lambda event: None, payload_type=int)
    with pytest.raises(ExceptionGroup):
        await bus.publish(Event(name="typed", payload="bad"))

    async def slow(event):
        await asyncio.Event().wait()

    bus.subscribe("slow", slow)
    with pytest.raises(ExceptionGroup):
        await bus.publish(Event(name="slow", payload=None))


async def test_cancellation_resistant_handler_is_detached_without_blocking_publication():
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def stubborn(event):
        nonlocal calls
        calls += 1
        started.set()
        if release.is_set():
            return
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    bus = EventBus(timeout=0.01)
    bus.subscribe("stubborn", stubborn)

    with pytest.raises(ExceptionGroup):
        await asyncio.wait_for(bus.publish(Event(name="stubborn", payload=None)), timeout=0.1)
    assert started.is_set()

    # The first invocation is still cancelling, so a second publication fails closed without
    # starting another handler task for the same subscription.
    with pytest.raises(ExceptionGroup):
        await bus.publish(Event(name="stubborn", payload=None))
    assert calls == 1

    release.set()
    for _ in range(20):
        await asyncio.sleep(0)
        if not bus._detached_callbacks:  # noqa: SLF001 - wait for test-owned callback cleanup.
            break
    assert not bus._detached_callbacks  # noqa: SLF001 - verify detached callback cleanup.
    await bus.publish(Event(name="stubborn", payload=None))
    assert calls == 2


async def test_retry_backoff_is_capped_by_bus_timeout():
    attempts = 0

    async def failing(event):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("failure")

    bus = EventBus(timeout=0.01)
    bus.subscribe(
        "bounded",
        failing,
        max_retries=2,
        backoff_initial=1e308,
        backoff_multiplier=10,
    )
    with pytest.raises(ExceptionGroup):
        await bus.publish(Event(name="bounded", payload=None))
    assert attempts == 3


async def test_retry_backoff_reaches_success_without_duplicate_subscriptions():
    attempts = 0

    def flaky(event):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ValueError("temporary")

    bus = EventBus()
    token = bus.subscribe("retry", flaky, max_retries=2)
    await bus.publish(Event(name="retry", payload=None))
    assert attempts == 3
    assert token.max_retries == 2
    assert bus.history[-1].failures == 0


async def test_dead_letter_policy_handles_terminal_failure():
    captured = []

    async def dead_letter(event, error):
        captured.append((event.name, type(error).__name__))

    bus = EventBus()
    bus.subscribe(
        "dead",
        lambda event: (_ for _ in ()).throw(ValueError("bad")),
        failure_policy=FailurePolicy.DEAD_LETTER,
        dead_letter=dead_letter,
    )
    await bus.publish(Event(name="dead", payload=1))
    assert captured == [("dead", "ValueError")]
    assert bus.history[-1].failures == 0


def test_event_contract_validates_correlation_and_delivery_metadata():
    correlation, causation = uuid4(), uuid4()
    event = Event(
        name="metadata",
        payload={"ok": True},
        correlation_id=correlation,
        causation_id=causation,
        delivery_key="message-1",
        priority=90,
    )
    assert event.correlation_id == correlation
    assert event.causation_id == causation
    with pytest.raises(ValueError):
        Event(name="metadata", payload=None, priority=101)
    with pytest.raises(ValueError):
        Event(name="metadata", payload=None, priority=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="printable"):
        Event(name="metadata", payload=None, delivery_key="bad\nkey")
    with pytest.raises(ValueError):
        Event(name=b"metadata", payload=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Event(name="metadata", payload=None, delivery_key=b"message-1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone"):
        Event(name="metadata", payload=None, occurred_at=datetime(2026, 1, 1))


async def test_delivery_key_deduplicates_replayed_events():
    calls = []
    bus = EventBus(history_size=2)
    bus.subscribe("idempotent", lambda event: calls.append(event.payload))
    await bus.publish(Event(name="idempotent", payload=1, delivery_key="k1"))
    await bus.publish(Event(name="idempotent", payload=2, delivery_key="k1"))
    assert calls == [1]
    assert bus.history[-1].deduplicated
    assert bus.stats == (2, 0, 1)


async def test_zero_history_disables_delivery_history_and_key_retention():
    """Zero history must not retain either diagnostics or delivery-key deduplication state."""
    calls = []
    bus = EventBus(history_size=0)
    bus.subscribe("ephemeral", lambda event: calls.append(event.payload))

    await bus.publish(Event(name="ephemeral", payload=1, delivery_key="same"))
    await bus.publish(Event(name="ephemeral", payload=2, delivery_key="same"))

    assert calls == [1, 2]
    assert bus.history == ()
    assert bus._delivery_key_set == set()  # noqa: SLF001 - verify bounded internal state.


async def test_predicate_filters_before_handler_execution():
    calls = []
    bus = EventBus()
    bus.subscribe(
        "filtered",
        lambda event: calls.append(event.payload),
        predicate=lambda event: event.payload % 2 == 0,
    )

    await bus.publish(Event(name="filtered", payload=1))
    await bus.publish(Event(name="filtered", payload=2))

    assert calls == [2]
    assert bus.history[-2].failures == 0


async def test_predicate_failure_is_reported_without_blocking_other_subscribers():
    calls = []
    bus = EventBus()

    def broken(event):
        raise RuntimeError("predicate failed")

    bus.subscribe("filtered", lambda event: calls.append(event.payload), predicate=broken)
    bus.subscribe("filtered", lambda event: calls.append("ok"))
    with pytest.raises(ExceptionGroup):
        await bus.publish(Event(name="filtered", payload=1))

    assert calls == ["ok"]


async def test_subscribers_are_delivered_by_priority_with_registration_tie_breaking():
    calls = []
    bus = EventBus()
    bus.subscribe("priority", lambda event: calls.append("normal"))
    high = bus.subscribe("priority", lambda event: calls.append("high"), priority=90)
    tie = bus.subscribe("priority", lambda event: calls.append("tie"), priority=90)

    await bus.publish(Event(name="priority", payload=None))
    assert calls == ["high", "tie", "normal"]
    assert high.priority == 90
    assert tie.priority == 90


async def test_handler_concurrency_is_bounded():
    import asyncio

    entered = 0
    peak = 0
    release = asyncio.Event()

    async def handler(event):
        nonlocal entered, peak
        entered += 1
        peak = max(peak, entered)
        await release.wait()
        entered -= 1

    bus = EventBus(max_concurrency=2)
    for _ in range(4):
        bus.subscribe("parallel", handler)
    task = asyncio.create_task(bus.publish(Event(name="parallel", payload=None)))
    for _ in range(20):
        await asyncio.sleep(0)
        if peak == 2:
            break
    assert peak == 2
    release.set()
    await task


def test_subscriber_priority_is_bounded():
    with pytest.raises(ValueError):
        EventBus().subscribe("priority", lambda event: None, priority=101)


def test_event_diagnostic_records_validate_public_boundaries() -> None:
    from orbit.events import Delivery, Subscription
    from orbit.types import new_event_id, new_subscription_id

    subscription = Subscription(new_subscription_id(), "orders", max_retries=2, priority=80)
    delivery = Delivery(new_event_id(), "orders", subscriber_count=2, failures=1)
    assert subscription.name == "orders"
    assert delivery.failures == 1

    with pytest.raises(TypeError, match="UUID"):
        Subscription("id", "orders")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Event names"):
        Delivery(new_event_id(), "Bad", 1, 0)
    with pytest.raises(ValueError, match="cannot exceed"):
        Delivery(new_event_id(), "orders", 1, 2)
    with pytest.raises(TypeError, match="boolean"):
        Delivery(new_event_id(), "orders", 1, 0, deduplicated=1)  # type: ignore[arg-type]


def test_event_transport_contract_is_runtime_checkable():
    class Transport:
        async def publish(self, event):
            return None

        async def subscribe(self, event_name, handler):
            return object()

        async def unsubscribe(self, token):
            return None

        async def close(self):
            return None

    assert isinstance(Transport(), EventTransport)


def test_event_subscription_rejects_invalid_callback_contracts() -> None:
    bus = EventBus()
    with pytest.raises(TypeError, match="subscriber"):
        bus.subscribe("test", None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="predicate"):
        bus.subscribe("test", lambda event: None, predicate=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="failure_policy"):
        bus.subscribe("test", lambda event: None, failure_policy="raise")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="payload_type"):
        bus.subscribe("test", lambda event: None, payload_type=object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_event_bus_validates_public_handles_and_event_values() -> None:
    bus = EventBus()
    with pytest.raises(TypeError, match="Subscription"):
        bus.unsubscribe(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Event"):
        await bus.publish(object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_event_bus_requires_boolean_predicate_decisions() -> None:
    bus = EventBus()
    bus.subscribe("typed", lambda event: None, predicate=lambda event: 1)  # type: ignore[arg-type]
    with pytest.raises(ExceptionGroup, match="delivery failed"):
        await bus.publish(Event(name="typed", payload=None))


@pytest.mark.asyncio
async def test_event_bus_validates_replay_arguments_before_store_access() -> None:
    from orbit.events import InMemoryEventStore

    bus = EventBus(store=InMemoryEventStore())
    with pytest.raises(ValueError):
        await bus.replay(after=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await bus.replay(limit=0)
    with pytest.raises(ValueError):
        await bus.replay(event_name="Invalid")


@pytest.mark.asyncio
async def test_event_bus_persists_and_replays_when_store_is_configured() -> None:
    from orbit.events import InMemoryEventStore

    store = InMemoryEventStore()
    bus = EventBus(store=store)
    await bus.publish(Event(name="persisted", payload={"ok": True}))
    replay = await bus.replay()
    assert len(replay) == 1
    assert replay[0].event.name == "persisted"
    assert replay[0].event.payload == {"ok": True}


@pytest.mark.asyncio
async def test_event_bus_replay_requires_store() -> None:
    bus = EventBus()
    with pytest.raises(RuntimeError, match="store"):
        await bus.replay()


@pytest.mark.asyncio
async def test_event_bus_deduplicates_before_durable_persistence() -> None:
    from orbit.events import InMemoryEventStore

    store = InMemoryEventStore()
    bus = EventBus(store=store, history_size=10)
    await bus.publish(Event(name="persisted", payload=1, delivery_key="same"))
    await bus.publish(Event(name="persisted", payload=2, delivery_key="same"))
    assert store.size == 1
    assert (await bus.replay())[0].event.payload == 1


@pytest.mark.asyncio
async def test_failed_durable_append_does_not_consume_delivery_key() -> None:
    class Store:
        def __init__(self):
            self.calls = 0

        async def append(self, event):
            self.calls += 1
            if self.calls == 1:
                raise OSError("temporary store failure")
            return self.calls

        async def read(self, **kwargs):
            return ()

        async def prune_before(self, sequence):
            return 0

        async def close(self):
            return None

    store = Store()
    bus = EventBus(store=store)
    event = Event(name="retryable", payload=None, delivery_key="key")
    with pytest.raises(OSError):
        await bus.publish(event)
    await bus.publish(event)
    assert store.calls == 2
