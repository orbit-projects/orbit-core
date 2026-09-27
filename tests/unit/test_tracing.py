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
"""Tests for context-propagated tracing contracts."""

import pytest

from orbit.diagnostics import InMemoryTracer, SpanRecord
from orbit.runtime.context import (
    bind_correlation_id,
    bind_request_id,
    bind_span_id,
    bind_trace_id,
    current_span_id,
    current_trace_id,
)


@pytest.mark.asyncio
async def test_in_memory_tracer_records_nested_parent_and_restores_context() -> None:
    tracer = InMemoryTracer()
    assert current_trace_id() is None
    async with tracer.start_span("request") as parent:
        assert current_trace_id() == parent.trace_id
        assert current_span_id() == parent.span_id
        parent.set_attribute("http.method", "GET")
        async with tracer.start_span("handler") as child:
            assert child.trace_id == parent.trace_id
            assert child.span_id != parent.span_id
    assert current_trace_id() is None
    assert current_span_id() is None
    assert tracer.history[0].parent_span_id == parent.span_id
    assert tracer.history[0].status == "ok"
    assert tracer.history[1].parent_span_id is None


@pytest.mark.asyncio
async def test_tracer_marks_exception_spans_as_error() -> None:
    tracer = InMemoryTracer()
    with pytest.raises(RuntimeError):
        async with tracer.start_span("failed"):
            raise RuntimeError("boom")
    assert tracer.history[0].status == "error"


@pytest.mark.asyncio
async def test_span_attributes_are_detached_and_immutable() -> None:
    tracer = InMemoryTracer()
    source = {"nested": {"value": 1}}
    async with tracer.start_span("request") as span:
        span.set_attribute("payload", source)
    source["nested"]["value"] = 2
    attributes = tracer.history[0].attributes
    assert attributes["payload"]["nested"]["value"] == 1
    with pytest.raises(TypeError, match="immutable"):
        attributes["payload"]["nested"]["value"] = 3


@pytest.mark.asyncio
async def test_span_metadata_limits_are_explicit() -> None:
    tracer = InMemoryTracer()
    with pytest.raises(ValueError, match="printable"):
        tracer.start_span("bad\nspan")
    async with tracer.start_span("request") as span:
        with pytest.raises(ValueError, match="printable"):
            span.set_attribute("bad\nattribute", True)
        with pytest.raises(ValueError, match="status"):
            span.set_status([])  # type: ignore[arg-type]
        for index in range(128):
            span.set_attribute(f"attribute-{index}", index)
        with pytest.raises(ValueError, match="cardinality"):
            span.set_attribute("attribute-overflow", True)


@pytest.mark.asyncio
async def test_span_context_is_restored_when_attribute_retention_fails() -> None:
    class Uncopyable:
        def __deepcopy__(self, memo: dict[int, object]) -> object:
            raise RuntimeError("cannot copy")

    tracer = InMemoryTracer()
    with pytest.raises(RuntimeError, match="cannot copy"):
        async with tracer.start_span("request") as span:
            span.set_attribute("uncopyable", Uncopyable())
    assert current_trace_id() is None
    assert current_span_id() is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"trace_id": "bad\ntrace"},
        {"span_id": ""},
        {"parent_span_id": "x" * 256},
        {"started_at": float("inf")},
        {"duration_seconds": -1},
        {"status": "unknown"},
        {"status": []},
    ],
)
def test_span_records_validate_public_snapshot_fields(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "name": "request",
        "trace_id": "trace",
        "span_id": "span",
        "parent_span_id": None,
        "started_at": 1.0,
        "duration_seconds": 0.1,
        "status": "ok",
    }
    values.update(kwargs)
    with pytest.raises((TypeError, ValueError)):
        SpanRecord(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("history_size", [True, "1"])
def test_tracer_rejects_invalid_history_size(history_size: object) -> None:
    with pytest.raises(ValueError):
        InMemoryTracer(history_size=history_size)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="1,000,000"):
        InMemoryTracer(history_size=1_000_001)


@pytest.mark.asyncio
async def test_zero_history_disables_span_retention() -> None:
    tracer = InMemoryTracer(history_size=0)
    async with tracer.start_span("discarded"):
        pass
    assert tracer.history == ()


@pytest.mark.parametrize("name", [None, 42, "bad\nspan", "x" * 256])
def test_tracer_rejects_non_string_names(name: object) -> None:
    tracer = InMemoryTracer()
    with pytest.raises(ValueError, match="Span names"):
        tracer.start_span(name)  # type: ignore[arg-type]


@pytest.mark.parametrize("binder", [bind_correlation_id, bind_trace_id, bind_span_id])
@pytest.mark.parametrize("value", [None, "", "x" * 256, "bad\nvalue", 42])
def test_context_binders_reject_invalid_identifiers(binder, value: object) -> None:
    """Task-local diagnostic context cannot retain malformed operator-facing IDs."""
    if value is None:
        binder(value)
        return
    with pytest.raises(ValueError):
        binder(value)  # type: ignore[arg-type]


def test_request_id_context_requires_a_uuid() -> None:
    """The runtime check preserves the RequestId NewType contract for dynamic callers."""
    with pytest.raises(TypeError, match="UUID"):
        bind_request_id("not-a-uuid")  # type: ignore[arg-type]
