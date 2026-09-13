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

import pytest

from orbit.events import Event, EventBus


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
