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
"""Application-level views used by diagnostics and administrative surfaces."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from orbit._limits import _MAX_CORE_CAPACITY
from orbit.state import ApplicationState


class ApplicationSummary(BaseModel):
    """A compact, serializable view of a running Orbit application."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    state: ApplicationState
    service_names: tuple[Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")], ...]

    @field_validator("service_names")
    @classmethod
    def validate_service_names(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Keep an operator summary within the same bound as the service registry."""
        if len(values) > _MAX_CORE_CAPACITY:
            raise ValueError("Application service summaries exceed Core's capacity limit.")
        return values


__all__ = ["ApplicationSummary"]
