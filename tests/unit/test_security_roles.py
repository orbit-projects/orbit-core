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
"""Role and token-scope text boundary tests."""

from collections.abc import Collection, Iterator

import pytest

from orbit import Application, ApplicationConfig
from orbit.routing import RouteMetadata
from orbit.security import Identity, Principal, Token
from orbit.security.roles import validate_role_collection


class _MisreportingRoles(Collection[str]):
    """Collection that reports no entries but yields more roles than the shared limit."""

    def __init__(self, count: int) -> None:
        self._count = count

    def __contains__(self, value: object) -> bool:
        return isinstance(value, str) and value.startswith("role-")

    def __iter__(self) -> Iterator[str]:
        return iter(f"role-{index}" for index in range(self._count))

    def __len__(self) -> int:
        return 0


@pytest.mark.parametrize("value", ["", "reader role", "reader\nrole", "reader\x7frole"])
def test_principal_rejects_unsafe_role_text(value: str) -> None:
    with pytest.raises((TypeError, ValueError), match="role|scope|identifier"):
        Principal(identity=Identity(subject="user", provider="test"), roles=frozenset({value}))


def test_principal_rejects_byte_role_coercion() -> None:
    with pytest.raises((TypeError, ValueError)):
        Principal(
            identity=Identity(subject="user", provider="test"),
            roles=frozenset({b"admin"}),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError):
        Identity(subject="user", provider="test", claims={b"iss": "issuer"})  # type: ignore[dict-item]


@pytest.mark.parametrize("value", ["reader\trole", "reader\nrole"])
def test_route_metadata_rejects_unsafe_role_text(value: str) -> None:
    with pytest.raises(ValueError, match="role|scope|identifier"):
        RouteMetadata(name="private", path="/private", method="GET", roles=frozenset({value}))


@pytest.mark.parametrize("value", ["scope\nvalue", "scope value"])
def test_token_rejects_unsafe_scope_text(value: str) -> None:
    with pytest.raises(ValueError, match="role|scope|identifier"):
        Token(
            token_id="token",
            subject="user",
            issued_at="2026-01-01T00:00:00Z",
            expires_at="2026-01-01T01:00:00Z",
            scopes=frozenset({value}),
        )


def test_route_role_validation_is_independent_of_application_composition() -> None:
    application = Application(ApplicationConfig(name="roles"))

    @application.router.route("/private", name="private", roles=frozenset({"orbit.admin.read"}))
    async def private(request):
        raise AssertionError("not called")

    application.validate()
    assert application.router.routes[0].metadata.roles == frozenset({"orbit.admin.read"})


def test_role_and_scope_collections_have_a_shared_cardinality_bound() -> None:
    """Every security surface rejects oversized role collections consistently."""
    with pytest.raises(ValueError, match="1,024"):
        Principal(
            identity=Identity(subject="user", provider="test"),
            roles=frozenset(f"role-{index}" for index in range(1_025)),
        )


def test_role_collection_rejects_unhashable_members_without_leaking_type_errors() -> None:
    """Malformed collection members become the shared public type error."""
    with pytest.raises(TypeError, match="collections of strings"):
        validate_role_collection([[]])  # type: ignore[list-item]


def test_role_collection_enforces_cardinality_during_materialization() -> None:
    """A false collection length cannot bypass the shared role and scope bound."""
    with pytest.raises(ValueError, match="1,024"):
        validate_role_collection(_MisreportingRoles(1_025))
