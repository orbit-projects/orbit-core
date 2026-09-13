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
"""Dependency container behavior tests."""

import pytest

from orbit.container import Container
from orbit.errors import ContainerError


def test_container_caches_singletons() -> None:
    """A singleton factory runs only once across repeated resolutions."""
    container = Container()
    created: list[object] = []
    container.register_factory("value", lambda _: created.append(object()) or created[-1])

    assert container.resolve("value") is container.resolve("value")
    assert len(created) == 1


def test_container_reports_circular_dependencies() -> None:
    """Circular factory resolution produces an actionable structured exception."""
    container = Container()
    container.register_factory("a", lambda current: current.resolve("b"))
    container.register_factory("b", lambda current: current.resolve("a"))

    with pytest.raises(ContainerError, match="Circular dependency"):
        container.resolve("a")
