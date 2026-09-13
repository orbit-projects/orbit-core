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

from collections.abc import Collection

from orbit.errors import ErrorCategory, OrbitProblem, SecurityError
from orbit.security.principal import Principal


def require_roles(principal: Principal | None, required: Collection[str]) -> None:
    """Require an authenticated principal possessing every requested role."""
    required_roles = frozenset(required)
    if principal is None or not required_roles.issubset(principal.roles):
        raise SecurityError(
            OrbitProblem(
                code="security.forbidden",
                message="The current principal is not authorized for this operation.",
                category=ErrorCategory.SECURITY,
                context={"required_roles": sorted(required_roles)},
            )
        )


__all__ = ["require_roles"]
