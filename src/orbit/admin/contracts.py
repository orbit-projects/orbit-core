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
"""Typed contract for trusted plugin administrative inspection contributions."""

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class AdminContribution(Protocol):
    """A read-only diagnostic page; the Core admin surface enforces authentication."""

    @property
    def name(self) -> str:
        """Return a unique lowercase slug under /admin/extensions/<name>."""

    async def inspect(self) -> BaseModel:
        """Return a Pydantic view model; secrets must use SecretStr/SecretBytes."""


__all__ = ["AdminContribution"]
