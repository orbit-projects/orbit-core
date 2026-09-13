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
"""Provider-neutral authenticated identity model."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Identity(BaseModel):
    """A verified identity from an external authentication provider."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str = Field(min_length=1, max_length=255)
    provider: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    claims: dict[str, Any] = Field(default_factory=dict)


__all__ = ["Identity"]
