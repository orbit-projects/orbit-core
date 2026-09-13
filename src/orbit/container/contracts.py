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
"""Container protocol for framework-neutral consumers."""

from typing import Any, Protocol

from orbit.container.dependency import DependencyKey


class ContainerContract(Protocol):
    """The dependency operations Orbit services may rely on."""

    def resolve(self, key: DependencyKey) -> Any:
        """Resolve a previously registered dependency."""


__all__ = ["ContainerContract"]
