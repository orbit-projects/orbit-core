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
"""Task-local application context for runtime integrations."""

from contextvars import ContextVar, Token
from typing import TYPE_CHECKING
from uuid import UUID

from orbit.types import RequestId

if TYPE_CHECKING:
    from orbit.application import Application

_application: ContextVar["Application | None"] = ContextVar("orbit_application", default=None)
_request_id: ContextVar[RequestId | None] = ContextVar("orbit_request_id", default=None)
_correlation_id: ContextVar[str | None] = ContextVar("orbit_correlation_id", default=None)
_trace_id: ContextVar[str | None] = ContextVar("orbit_trace_id", default=None)
_span_id: ContextVar[str | None] = ContextVar("orbit_span_id", default=None)


def _validate_optional_context_id(value: str | None, label: str) -> str | None:
    """Validate identifiers before they enter task-local logging and tracing context."""
    if value is not None and (
        not isinstance(value, str)
        or not 1 <= len(value) <= 255
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{label} must be a bounded printable string when provided.")
    return value


def current_request_id() -> RequestId | None:
    """Read the typed server-generated request ID in handler or middleware context."""
    return _request_id.get()


def bind_request_id(request_id: RequestId) -> Token[RequestId | None]:
    """Bind a typed request ID and return the token needed to restore prior context."""
    if not isinstance(request_id, UUID):
        raise TypeError("Request IDs must be UUID values.")
    return _request_id.set(request_id)


def reset_request_id(token: Token[RequestId | None]) -> None:
    """Restore the context after response delivery and resource cleanup."""
    _request_id.reset(token)


def current_correlation_id() -> str | None:
    """Read the causally related operation identifier for the current context."""
    return _correlation_id.get()


def bind_correlation_id(value: str | None) -> Token[str | None]:
    """Bind a correlation identifier and return its restoration token."""
    return _correlation_id.set(_validate_optional_context_id(value, "Correlation IDs"))


def reset_correlation_id(token: Token[str | None]) -> None:
    """Restore the previous correlation identifier."""
    _correlation_id.reset(token)


def current_trace_id() -> str | None:
    """Read the distributed trace identifier for the current context."""
    return _trace_id.get()


def bind_trace_id(value: str | None) -> Token[str | None]:
    """Bind a trace identifier and return its restoration token."""
    return _trace_id.set(_validate_optional_context_id(value, "Trace IDs"))


def reset_trace_id(token: Token[str | None]) -> None:
    """Restore the previous trace identifier."""
    _trace_id.reset(token)


def current_span_id() -> str | None:
    """Read the current span identifier."""
    return _span_id.get()


def bind_span_id(value: str | None) -> Token[str | None]:
    """Bind a span identifier and return its restoration token."""
    return _span_id.set(_validate_optional_context_id(value, "Span IDs"))


def reset_span_id(token: Token[str | None]) -> None:
    """Restore the previous span identifier."""
    _span_id.reset(token)


def current_application() -> "Application | None":
    """Return the application bound to the current asynchronous context."""
    return _application.get()


def bind_application(application: "Application") -> Token["Application | None"]:
    """Bind an application to the current context and return its reset token."""
    return _application.set(application)


def reset_application(token: Token["Application | None"]) -> None:
    """Restore the application context represented by ``token``."""
    _application.reset(token)


__all__ = [
    "bind_application",
    "current_application",
    "reset_application",
    "bind_request_id",
    "current_request_id",
    "reset_request_id",
    "bind_correlation_id",
    "current_correlation_id",
    "reset_correlation_id",
    "bind_trace_id",
    "current_trace_id",
    "reset_trace_id",
    "bind_span_id",
    "current_span_id",
    "reset_span_id",
]
