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
"""Minimal ASGI type aliases retained without a third-party framework dependency."""

from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

Scope: TypeAlias = dict[str, Any]
Message: TypeAlias = dict[str, Any]
Receive: TypeAlias = Callable[[], Awaitable[Message]]
Send: TypeAlias = Callable[[Message], Awaitable[None]]

__all__ = ["Message", "Receive", "Scope", "Send"]
