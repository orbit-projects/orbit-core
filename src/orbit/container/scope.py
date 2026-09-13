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
"""Provider lifetimes and the boundaries that own their resources."""

from enum import StrEnum


class Scope(StrEnum):
    """SINGLETON lives with the application; SCOPED with a request; TRANSIENT per call."""

    SINGLETON = "singleton"
    SCOPED = "scoped"
    TRANSIENT = "transient"


__all__ = ["Scope"]
