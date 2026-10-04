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
"""Small role-based authorization primitive owned by Orbit Core."""

import inspect
import re
from collections.abc import Awaitable, Callable, Collection
from typing import Any

from orbit.errors import ErrorCategory, OrbitProblem, SecurityError
from orbit.security.principal import Principal
from orbit.security.roles import validate_role_collection

_MAX_POLICIES = 10_000
PolicyEvaluator = Callable[[Principal, Any], bool | Awaitable[bool]]


def require_roles(principal: Principal | None, required: Collection[str]) -> None:
    """Require an authenticated principal possessing every requested role."""
    if principal is not None and not isinstance(principal, Principal):
        raise TypeError("principal must be a Principal instance or None.")
    required_roles = _validate_roles(required)
    if principal is None or not required_roles.issubset(principal.roles):
        raise SecurityError(
            OrbitProblem(
                code="security.forbidden",
                message="The current principal is not authorized for this operation.",
                category=ErrorCategory.SECURITY,
                context={"required_roles": sorted(required_roles)},
            )
        )


class PolicyEngine:
    """Explicit, provider-neutral policy registry for resource-level authorization."""

    def __init__(self) -> None:
        self._policies: dict[str, PolicyEvaluator] = {}
        self._frozen = False

    def register(self, name: str, evaluator: PolicyEvaluator, *, replace: bool = False) -> None:
        """Register a policy evaluator during composition."""
        if self._frozen:
            raise RuntimeError("Authorization policies are frozen.")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9.-]{0,126}", name):
            raise ValueError("Policy names must be lowercase and contain [a-z0-9.-].")
        if not isinstance(replace, bool):
            raise TypeError("replace must be a boolean.")
        if name in self._policies and not replace:
            raise ValueError(f"Policy {name!r} is already registered.")
        if name not in self._policies and len(self._policies) >= _MAX_POLICIES:
            raise RuntimeError("Authorization policy capacity reached.")
        if not callable(evaluator):
            raise TypeError("Policy evaluator must be callable.")
        self._policies[name] = evaluator

    def register_roles(self, name: str, roles: Collection[str], *, replace: bool = False) -> None:
        """Register a policy requiring every listed role."""
        required = _validate_roles(roles)
        self.register(
            name, lambda principal, _: required.issubset(principal.roles), replace=replace
        )

    def freeze(self) -> None:
        """Prevent policy mutation after application composition."""
        self._frozen = True

    @property
    def policies(self) -> tuple[str, ...]:
        """Return deterministic policy names for inspection."""
        return tuple(self._policies)

    async def authorize(
        self, principal: Principal | None, policy: str, resource: Any = None
    ) -> bool:
        """Evaluate a named policy; anonymous or unknown policies are denied."""
        if principal is not None and not isinstance(principal, Principal):
            raise TypeError("principal must be a Principal instance or None.")
        if not isinstance(policy, str):
            raise TypeError("Policy names must be strings.")
        if re.fullmatch(r"[a-z][a-z0-9.-]{0,126}", policy) is None:
            raise ValueError("Policy names must be lowercase and contain [a-z0-9.-].")
        if principal is None:
            return False
        evaluator = self._policies.get(policy)
        if evaluator is None:
            return False
        result = evaluator(principal, resource)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, bool) else False

    async def require(self, principal: Principal | None, policy: str, resource: Any = None) -> None:
        """Raise a uniform security error when a policy does not grant access."""
        if not await self.authorize(principal, policy, resource):
            raise SecurityError(
                OrbitProblem(
                    code="security.forbidden",
                    message="The current principal is not authorized for this operation.",
                    category=ErrorCategory.SECURITY,
                    context={"policy": policy},
                )
            )


__all__ = ["PolicyEngine", "require_roles"]


def _validate_roles(roles: Collection[str]) -> frozenset[str]:
    """Validate role collections without treating a single string as characters."""
    return validate_role_collection(roles)
