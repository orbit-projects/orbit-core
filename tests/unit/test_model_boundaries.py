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
"""Nested mutability regressions for Core's public structured models."""

from datetime import UTC, datetime
from types import MappingProxyType

import pytest

from orbit.admin.models import AdminAuditRecord
from orbit.application.models import ApplicationSummary
from orbit.container import Scope
from orbit.diagnostics.inspection import CompositionSnapshot, ProviderDescription
from orbit.errors import ErrorCategory, ErrorResponse, OrbitProblem
from orbit.events import Event
from orbit.health import HealthReport
from orbit.security import OAuthTokenResponse
from orbit.services import ServiceDescriptor
from orbit.state import ApplicationState
from orbit.state.models import ComponentState
from orbit.types import new_application_id, new_configuration_id, new_provider_id


@pytest.mark.parametrize(
    "factory, field",
    [
        (
            lambda mapping: Event(name="orders.created", payload={}, metadata=mapping),
            "metadata",
        ),
        (lambda mapping: ServiceDescriptor(name="orders", metadata=mapping), "metadata"),
        (lambda mapping: HealthReport(details=mapping), "details"),
        (
            lambda mapping: OrbitProblem(
                code="runtime.failed",
                message="failed",
                category=ErrorCategory.RUNTIME,
                context=mapping,
            ),
            "context",
        ),
    ],
)
def test_structured_model_mappings_are_detached_and_immutable(factory, field: str) -> None:
    """Frozen models reject nested mutation and do not retain caller-owned mappings."""
    source = {"nested": {"value": 1}}
    model = factory(source)
    source["nested"]["value"] = 2
    value = getattr(model, field)
    assert value["nested"]["value"] == 1
    with pytest.raises(TypeError, match="immutable"):
        value["nested"]["value"] = 3


@pytest.mark.parametrize(
    "factory",
    [
        lambda mapping: Event(name="orders.created", payload={}, metadata=mapping),
        lambda mapping: ServiceDescriptor(name="orders", metadata=mapping),
        lambda mapping: OrbitProblem(
            code="runtime.failed",
            message="failed",
            category=ErrorCategory.RUNTIME,
            context=mapping,
        ),
        lambda mapping: OrbitProblem(
            code="runtime.failed",
            message="failed",
            category=ErrorCategory.RUNTIME,
            metadata=mapping,
        ),
    ],
)
@pytest.mark.parametrize(
    "mapping", [{"unsafe\nkey": True}, {str(index): True for index in range(2_049)}]
)
def test_structured_model_mappings_reject_unsafe_or_unbounded_keys(factory, mapping) -> None:
    """Retained Core mappings have bounded, printable key contracts."""
    with pytest.raises(ValueError):
        factory(mapping)
    with pytest.raises(ValueError):
        factory({b"unsafe": True})  # type: ignore[dict-item]


def test_structured_error_metadata_is_immutable() -> None:
    problem = OrbitProblem(
        code="runtime.failed",
        message="failed",
        category=ErrorCategory.RUNTIME,
        metadata={"trace": {"id": "abc"}},
    )
    with pytest.raises(TypeError, match="immutable"):
        problem.metadata["trace"]["id"] = "changed"


def test_structured_models_reject_cyclic_or_excessively_nested_values() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(ValueError, match="cyclic"):
        Event(name="orders.created", payload={}, metadata=cyclic)

    nested: object = "leaf"
    for _ in range(64):
        nested = [nested]
    with pytest.raises(ValueError, match="nested"):
        HealthReport(details={"nested": nested})


def test_structured_models_freeze_nested_mapping_implementations() -> None:
    model = Event(
        name="orders.created",
        payload={},
        metadata={"nested": MappingProxyType({"value": 1})},
    )
    with pytest.raises(TypeError, match="immutable"):
        model.metadata["nested"]["value"] = 2


def test_structured_models_enforce_recursive_work_budget(monkeypatch) -> None:
    import orbit._immutability as immutability

    monkeypatch.setattr(immutability, "_MAX_FREEZE_ITEMS", 3)
    with pytest.raises(ValueError, match="items"):
        Event(name="orders.created", payload={}, metadata={"items": [1, 2]})


@pytest.mark.parametrize(
    "factory",
    [
        lambda: OrbitProblem(
            code="runtime.failed",
            message="bad\nmessage",
            category=ErrorCategory.RUNTIME,
        ),
        lambda: OrbitProblem(
            code="runtime.failed",
            message="x" * 1025,
            category=ErrorCategory.RUNTIME,
        ),
        lambda: ErrorResponse(code="Bad Code"),
        lambda: ErrorResponse(code="runtime.failed", message="bad\x7fmessage"),
    ],
)
def test_structured_error_text_is_bounded_and_printable(factory) -> None:
    """Shared error models cannot carry unsafe text into HTTP or diagnostics surfaces."""
    with pytest.raises(ValueError):
        factory()


def test_health_report_keeps_aware_timestamp() -> None:
    report = HealthReport(checked_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert report.checked_at.tzinfo is UTC
    with pytest.raises(ValueError):
        HealthReport(details={b"unsafe": True})  # type: ignore[dict-item]


def test_runtime_models_reject_coerced_operational_values() -> None:
    with pytest.raises(ValueError):
        ApplicationState(application_id=new_application_id(), service_count=True)
    with pytest.raises(ValueError):
        AdminAuditRecord(action="service.restart", success=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        OAuthTokenResponse(access_token="token", expires_in="60")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AdminAuditRecord(action=b"service.restart", success=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        OrbitProblem(
            code=b"runtime.failed",  # type: ignore[arg-type]
            message="failed",
            category=ErrorCategory.RUNTIME,
        )


def test_operator_snapshots_reject_unsafe_component_names() -> None:
    """State and administrative summaries cannot carry malformed operator identifiers."""
    with pytest.raises(ValueError):
        ComponentState(name="service\nname")
    with pytest.raises(ValueError):
        ApplicationSummary(
            state=ApplicationState(application_id=new_application_id()),
            service_names=("service\nname",),
        )


def test_composition_snapshots_detach_configuration_and_validate_provider_text() -> None:
    configuration = {"application": {"name": "demo"}}
    snapshot = CompositionSnapshot(
        services=(),
        plugins=(),
        routes=(),
        dependencies=(
            ProviderDescription(
                id=new_provider_id(),
                key="service",
                scope=Scope.SINGLETON,
                dependencies=(),
                resource=False,
            ),
        ),
        configuration_id=new_configuration_id(),
        configuration=configuration,
    )
    configuration["application"]["name"] = "changed"
    assert snapshot.configuration["application"]["name"] == "demo"
    with pytest.raises(TypeError, match="immutable"):
        snapshot.configuration["application"]["name"] = "changed"  # type: ignore[index]
    with pytest.raises(ValueError, match="printable"):
        ProviderDescription(
            id=new_provider_id(),
            key="bad\nprovider",
            scope=Scope.SINGLETON,
            dependencies=(),
            resource=False,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target": "service\nname"},
        {"subject": "operator\x7f"},
        {"provider": "test\tprovider"},
        {"error_code": "provider failure"},
        {"error_code": "x" * 128},
        {"occurred_at": datetime(2026, 1, 1)},
    ],
)
def test_admin_audit_records_reject_unsafe_or_ambiguous_fields(
    kwargs: dict[str, object],
) -> None:
    """Audit records remain safe to render and comparable across deployment time zones."""
    with pytest.raises(ValueError):
        AdminAuditRecord(action="service.restart", success=True, **kwargs)  # type: ignore[arg-type]
