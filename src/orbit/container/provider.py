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
"""Explicit provider definitions; runtime instances are owned by containers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from orbit.container.dependency import DependencyKey
from orbit.container.scope import Scope

if TYPE_CHECKING:
    from orbit.container.container import Container


@dataclass(frozen=True)
class Provider:
    """A factory, declared dependencies, lifetime and context-manager ownership."""

    factory: Callable[[Container], Any]
    scope: Scope = Scope.SINGLETON
    dependencies: tuple[DependencyKey, ...] = ()
    resource: bool = False


__all__ = ["Provider"]
