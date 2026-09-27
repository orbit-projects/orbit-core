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
"""Typed keys used for dependency registration and resolution."""

from typing import Any, TypeAlias

DependencyKey: TypeAlias = type[Any] | str


def _validate_dependency_key(key: DependencyKey) -> None:
    """Reject malformed names before they reach provider state or diagnostics."""
    if isinstance(key, str):
        if not key or len(key) > 255:
            raise ValueError(
                "Dependency string keys must be nonempty and bounded to 255 characters."
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in key):
            raise ValueError("Dependency string keys must not contain control characters.")
        return
    if not isinstance(key, type):
        raise TypeError("Dependency keys must be types or nonempty strings.")


__all__ = ["DependencyKey"]
