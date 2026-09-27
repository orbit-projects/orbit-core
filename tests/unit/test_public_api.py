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
"""Regression checks for documented focused-package imports and composition limits."""

import pytest


def test_security_authenticator_is_exported_from_focused_package() -> None:
    """The documented provider-neutral authentication contract has a stable import path."""
    from orbit.security import Authenticator

    assert Authenticator.__name__ == "Authenticator"


def test_core_contracts_are_exported_from_focused_packages() -> None:
    """Extension authors can import the documented Core contracts without private modules."""
    from orbit.admin import AdminContribution
    from orbit.asgi import Middleware, NextHandler
    from orbit.container import ContainerContract, DependencyKey
    from orbit.health import HealthCheck
    from orbit.lifecycle import LifecycleObserver
    from orbit.plugins import PluginContract
    from orbit.services import ServiceContract

    for contract in (
        AdminContribution,
        Middleware,
        NextHandler,
        ContainerContract,
        DependencyKey,
        HealthCheck,
        LifecycleObserver,
        PluginContract,
        ServiceContract,
    ):
        assert contract is not None


def test_application_builder_service_capacity_is_bounded(monkeypatch) -> None:
    """The fluent composition helper cannot retain an unbounded service list."""
    from orbit.application import ApplicationBuilder
    from orbit.config import ApplicationConfig

    monkeypatch.setattr("orbit.application.builder._MAX_CORE_CAPACITY", 1)
    builder = ApplicationBuilder(ApplicationConfig(name="builder-capacity"))
    builder.service(object())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="capacity"):
        builder.service(object())  # type: ignore[arg-type]
