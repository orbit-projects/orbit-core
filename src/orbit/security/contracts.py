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
"""Authentication and authorization extension contracts."""

from typing import Protocol, runtime_checkable

from orbit.asgi.request import Request
from orbit.security.principal import Principal


@runtime_checkable
class Authenticator(Protocol):
    """Authenticate an ASGI request through an optional provider plugin."""

    async def authenticate(self, request: Request) -> Principal | None:
        """Return the verified principal, or ``None`` for anonymous requests."""


__all__ = ["Authenticator"]
