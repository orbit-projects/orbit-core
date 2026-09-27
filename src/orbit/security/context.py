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
"""Task-local security context for runtime and administrative operations."""

from contextvars import ContextVar, Token

from orbit.security.principal import Principal

_principal: ContextVar[Principal | None] = ContextVar("orbit_principal", default=None)


def current_principal() -> Principal | None:
    """Return the principal bound to the current asynchronous context."""
    return _principal.get()


def bind_principal(principal: Principal | None) -> Token[Principal | None]:
    """Bind a principal for the current context and return a reset token."""
    if principal is not None and not isinstance(principal, Principal):
        raise TypeError("Security context values must be Principal instances or None.")
    return _principal.set(principal)


def reset_principal(token: Token[Principal | None]) -> None:
    """Restore the principal context captured by ``bind_principal``."""
    _principal.reset(token)


__all__ = ["bind_principal", "current_principal", "reset_principal"]
