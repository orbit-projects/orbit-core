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
"""Verify deny-by-default role and resource policy evaluation."""

import pytest

from orbit.errors import SecurityError
from orbit.security import Identity, PolicyEngine, Principal


def principal(*roles: str) -> Principal:
    return Principal(
        identity=Identity(subject="user-1", provider="test"),
        roles=frozenset(roles),
    )


@pytest.mark.asyncio
async def test_policy_engine_supports_roles_and_resource_predicates():
    engine = PolicyEngine()
    engine.register_roles("admin", {"admin"})
    engine.register(
        "owner", lambda current, resource: current.identity.subject == resource["owner"]
    )
    engine.freeze()

    assert await engine.authorize(principal("admin"), "admin")
    assert await engine.authorize(principal(), "owner", {"owner": "user-1"})
    assert not await engine.authorize(principal(), "missing")
    assert engine.policies == ("admin", "owner")
    with pytest.raises(RuntimeError):
        engine.register_roles("late", {"admin"})


@pytest.mark.asyncio
async def test_policy_engine_requires_authorization():
    engine = PolicyEngine()
    engine.register_roles("admin", {"admin"})

    with pytest.raises(SecurityError, match="not authorized"):
        await engine.require(principal("user"), "admin")


def test_policy_contracts_reject_invalid_roles_and_replacement_flags():
    engine = PolicyEngine()
    with pytest.raises(TypeError, match="Roles"):
        engine.register_roles("bad", "admin")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="nonempty"):
        engine.register_roles("bad", {""})
    with pytest.raises(TypeError, match="boolean"):
        engine.register("bad", lambda current, resource: True, replace=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Policy names"):
        engine.register(1, lambda current, resource: True)  # type: ignore[arg-type]


def test_policy_engine_bounds_policy_cardinality(monkeypatch) -> None:
    """Policy registration cannot grow an unbounded evaluator registry."""
    monkeypatch.setattr("orbit.security.authorization._MAX_POLICIES", 1)
    engine = PolicyEngine()
    engine.register("first", lambda current, resource: True)
    with pytest.raises(RuntimeError, match="capacity"):
        engine.register("second", lambda current, resource: True)
    engine.register("first", lambda current, resource: False, replace=True)


def test_identity_subject_rejects_control_characters() -> None:
    with pytest.raises(ValueError, match="control"):
        Identity(subject="user\n1", provider="test")


@pytest.mark.asyncio
async def test_policy_engine_does_not_accept_truthy_non_boolean_results():
    engine = PolicyEngine()
    engine.register("truthy", lambda current, resource: "allowed")  # type: ignore[return-value]
    assert await engine.authorize(principal("admin"), "truthy") is False
    with pytest.raises(TypeError, match="Policy names"):
        await engine.authorize(principal("admin"), [])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Policy names"):
        await engine.authorize(principal("admin"), "bad policy")
