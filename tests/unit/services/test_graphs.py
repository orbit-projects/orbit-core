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
"""Service registration and graph validation independent of lifecycle execution."""

import pytest

from orbit import Service, ServiceDescriptor
from orbit.errors import ValidationError
from orbit.services import ServiceRegistry
from orbit.types import new_service_id


def service(name, dependencies=()):
    instance = Service()
    instance.descriptor = ServiceDescriptor(name=name, dependencies=dependencies)
    return instance


def test_duplicate_name_is_rejected():
    registry = ServiceRegistry()
    registry.register(service("one"))
    with pytest.raises(ValidationError):
        registry.register(service("one"))
    with pytest.raises(ValidationError):
        registry.register(object())


def test_missing_and_cyclic_dependencies():
    registry = ServiceRegistry()
    registry.register(service("missing", (new_service_id(),)))
    with pytest.raises(ValidationError):
        registry.ordered()
    registry = ServiceRegistry()
    a, b = service("one"), service("two")
    a.descriptor = a.descriptor.model_copy(update={"dependencies": (b.descriptor.id,)})
    b.descriptor = b.descriptor.model_copy(update={"dependencies": (a.descriptor.id,)})
    registry.register(a)
    registry.register(b)
    with pytest.raises(ValidationError):
        registry.ordered()
    assert registry.get(a.descriptor.id) is a
