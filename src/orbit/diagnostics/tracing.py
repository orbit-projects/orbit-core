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
"""Provider-neutral tracing contracts and a bounded in-process tracer."""

from __future__ import annotations

import secrets
from collections import deque
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from contextvars import Token
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Protocol, runtime_checkable

from orbit._immutability import freeze_mapping
from orbit._limits import _MAX_CORE_CAPACITY, is_finite_number
from orbit.runtime.context import (
    bind_span_id,
    bind_trace_id,
    current_span_id,
    current_trace_id,
    reset_span_id,
    reset_trace_id,
)


def _id(length: int) -> str:
    return secrets.token_hex(length // 2)


_MAX_SPAN_ATTRIBUTES = 128


@dataclass(frozen=True)
class SpanRecord:
    """Detached span data suitable for exporters and diagnostics."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    started_at: float
    duration_seconds: float
    status: str
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate bounded span metadata and freeze attributes retained for exporters."""
        for value, label in (
            (self.name, "Span names"),
            (self.trace_id, "Trace IDs"),
            (self.span_id, "Span IDs"),
        ):
            if (
                not isinstance(value, str)
                or not 1 <= len(value) <= 255
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError(f"{label} must be bounded printable strings.")
        if self.parent_span_id is not None and (
            not isinstance(self.parent_span_id, str)
            or not 1 <= len(self.parent_span_id) <= 255
            or any(
                ord(character) < 32 or ord(character) == 127 for character in self.parent_span_id
            )
        ):
            raise ValueError("Parent span IDs must be bounded printable strings.")
        if not is_finite_number(self.started_at) or not is_finite_number(self.duration_seconds):
            raise ValueError("Span timestamps must be finite numbers.")
        if self.duration_seconds < 0:
            raise ValueError("Span duration cannot be negative.")
        if not isinstance(self.status, str) or self.status not in {"unset", "ok", "error"}:
            raise ValueError("Span status must be unset, ok, or error.")
        object.__setattr__(self, "started_at", float(self.started_at))
        object.__setattr__(self, "duration_seconds", float(self.duration_seconds))
        if not isinstance(self.attributes, Mapping):
            raise TypeError("Span attributes must be a mapping.")
        if len(self.attributes) > _MAX_SPAN_ATTRIBUTES:
            raise ValueError("Span attributes exceed the configured cardinality limit.")
        if any(
            not isinstance(name, str)
            or not name
            or len(name) > 255
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
            for name in self.attributes
        ):
            raise ValueError("Span attribute names must be bounded printable strings.")
        object.__setattr__(self, "attributes", freeze_mapping(dict(self.attributes)))


@runtime_checkable
class Span(Protocol):
    """Mutable span boundary implemented by tracing adapters.

    Core tracks task-local parentage and lifecycle status. Exporters, sampling, network
    propagation, and vendor-specific attributes belong to a tracing plugin.
    """

    @property
    def trace_id(self) -> str:
        """Return the trace identifier shared by spans in one distributed operation."""

    @property
    def span_id(self) -> str:
        """Return this span's unique identifier."""

    def set_attribute(self, name: str, value: Any) -> None:
        """Attach a bounded, exporter-safe attribute to the active span."""

    def set_status(self, status: str) -> None:
        """Set the span status to ``unset``, ``ok``, or ``error``."""


@runtime_checkable
class Tracer(Protocol):
    """Create spans while preserving W3C trace context through context variables."""

    def start_span(self, name: str) -> AbstractAsyncContextManager[Span]:
        """Create an async context manager that binds and records a child span."""


class InMemoryTracer:
    """Bounded process-local tracer for diagnostics and adapter contract tests.

    Records are detached and capped by ``history_size``; this is not a distributed tracing
    backend or an OpenTelemetry exporter.
    """

    def __init__(self, *, history_size: int = 1000) -> None:
        if (
            isinstance(history_size, bool)
            or not isinstance(history_size, int)
            or not 0 <= history_size <= _MAX_CORE_CAPACITY
        ):
            raise ValueError("history_size must be between 0 and 1,000,000.")
        self._history: deque[SpanRecord] = deque(maxlen=history_size)

    @property
    def history(self) -> tuple[SpanRecord, ...]:
        """Return retained spans in completion order as an immutable tuple."""
        return tuple(self._history)

    def start_span(self, name: str) -> AbstractAsyncContextManager[Span]:
        """Create a bounded in-memory span and validate its diagnostic name."""
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 255
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
        ):
            raise ValueError("Span names must be bounded printable strings.")
        return _MemorySpan(self, name)

    def _record(self, record: SpanRecord) -> None:
        self._history.append(record)


class _MemorySpan(AbstractAsyncContextManager["_MemorySpan"]):
    def __init__(self, tracer: InMemoryTracer, name: str) -> None:
        self._tracer = tracer
        self._name = name
        self._trace_id = current_trace_id() or _id(32)
        self._span_id = _id(16)
        self._parent = current_span_id()
        self._started = monotonic()
        self._status = "unset"
        self._attributes: dict[str, Any] = {}
        self._trace_token: Token[str | None] | None = None
        self._span_token: Token[str | None] | None = None

    @property
    def trace_id(self) -> str:
        """Return this span's trace identifier."""
        return self._trace_id

    @property
    def span_id(self) -> str:
        """Return this span's unique identifier."""
        return self._span_id

    def set_attribute(self, name: str, value: Any) -> None:
        """Record one bounded attribute for the eventual detached span record."""
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 255
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
        ):
            raise ValueError("Span attribute names must be bounded printable strings.")
        if name not in self._attributes and len(self._attributes) >= _MAX_SPAN_ATTRIBUTES:
            raise ValueError("Span attributes exceed the configured cardinality limit.")
        self._attributes[name] = value

    def set_status(self, status: str) -> None:
        """Set the span status used by diagnostics and exporter adapters."""
        if not isinstance(status, str) or status not in {"unset", "ok", "error"}:
            raise ValueError("Span status must be unset, ok, or error.")
        self._status = status

    async def __aenter__(self) -> _MemorySpan:
        self._trace_token = bind_trace_id(self._trace_id)
        self._span_token = bind_span_id(self._span_id)
        return self

    async def __aexit__(self, exc_type: object, exc: BaseException | None, tb: object) -> None:
        try:
            if exc is not None:
                self._status = "error"
            elif self._status == "unset":
                self._status = "ok"
            self._tracer._record(
                SpanRecord(
                    self._name,
                    self._trace_id,
                    self._span_id,
                    self._parent,
                    self._started,
                    monotonic() - self._started,
                    self._status,
                    dict(self._attributes),
                )
            )
        finally:
            if self._span_token is not None:
                reset_span_id(self._span_token)
            if self._trace_token is not None:
                reset_trace_id(self._trace_token)


__all__ = ["InMemoryTracer", "Span", "SpanRecord", "Tracer"]
