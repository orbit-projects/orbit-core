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

if TYPE_CHECKING:
    from orbit.application import Application

_application: ContextVar["Application | None"] = ContextVar("orbit_application", default=None)
_request_id: ContextVar[str | None] = ContextVar("orbit_request_id", default=None)


def current_request_id() -> str | None:
    """Read the server-generated correlation ID in a handler, resource or middleware."""
    return _request_id.get()


def bind_request_id(request_id: str) -> Token[str | None]:
    """Bind a request correlation ID and return its restoration token."""
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the context after response delivery and resource cleanup."""
    _request_id.reset(token)


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
]
