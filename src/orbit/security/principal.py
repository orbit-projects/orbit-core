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
"""Authorization principal derived from a verified identity."""

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from orbit.security.identity import Identity
from orbit.security.roles import validate_role_collection


class Principal(BaseModel):
    """An identity and the roles Core may use for authorization decisions."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    identity: Identity
    roles: frozenset[StrictStr] = Field(default_factory=frozenset)

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, value: frozenset[str]) -> frozenset[str]:
        """Reject unsafe role text before it reaches authorization or audit output."""
        return validate_role_collection(value)


__all__ = ["Principal"]
