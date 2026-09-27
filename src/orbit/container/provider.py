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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import UUID

from orbit._limits import _MAX_RELATION_ENTRIES
from orbit.container.dependency import DependencyKey, _validate_dependency_key
from orbit.container.scope import Scope
from orbit.types import ProviderId, new_provider_id

if TYPE_CHECKING:
    from orbit.container.container import Container


@dataclass(frozen=True)
class Provider:
    """Validated factory metadata, dependencies, lifetime and resource ownership."""

    factory: Callable[[Container], Any]
    scope: Scope = Scope.SINGLETON
    dependencies: tuple[DependencyKey, ...] = ()
    resource: bool = False
    id: ProviderId = field(default_factory=new_provider_id)

    def __post_init__(self) -> None:
        """Validate provider metadata even when an integration constructs it directly."""
        if not callable(self.factory):
            raise TypeError("Provider factories must be callable.")
        if not isinstance(self.scope, Scope):
            raise TypeError("Provider scope must be a Scope.")
        if not isinstance(self.dependencies, tuple):
            raise TypeError("Provider dependencies must be a tuple of keys.")
        if len(self.dependencies) > _MAX_RELATION_ENTRIES:
            raise ValueError(
                f"Provider dependencies cannot contain more than {_MAX_RELATION_ENTRIES:,} entries."
            )
        for key in self.dependencies:
            _validate_dependency_key(key)
        if not isinstance(self.resource, bool):
            raise TypeError("Provider resource must be a boolean.")
        if not isinstance(self.id, UUID):
            raise TypeError("Provider IDs must be UUID-backed identifiers.")


__all__ = ["Provider"]
