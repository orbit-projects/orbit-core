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
"""Versioned snapshots of application and component lifecycle state."""

from pydantic import BaseModel, ConfigDict, Field

from orbit.health import HealthStatus
from orbit.lifecycle import LifecyclePhase
from orbit.types import ApplicationId


class ComponentState(BaseModel):
    """State for a named service or plugin, including partial-startup failures."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    name: str
    phase: LifecyclePhase = LifecyclePhase.CREATED
    health: HealthStatus = HealthStatus.UNKNOWN


class ApplicationState(BaseModel):
    """A detached, runtime-validated snapshot shared by all operator surfaces."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    application_id: ApplicationId
    phase: LifecyclePhase = LifecyclePhase.CREATED
    service_count: int = Field(default=0, ge=0)
    health: HealthStatus = HealthStatus.UNKNOWN
    revision: int = Field(default=0, ge=0)
    services: tuple[ComponentState, ...] = ()
    plugins: tuple[ComponentState, ...] = ()


__all__ = ["ApplicationState", "ComponentState"]
