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
"""Validated lifecycle transition data."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from orbit.lifecycle.phase import LifecyclePhase


class LifecycleTransition(BaseModel):
    """A completed state transition captured for observers and diagnostics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    previous: LifecyclePhase
    current: LifecyclePhase
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


__all__ = ["LifecycleTransition"]
