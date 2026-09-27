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
"""Runtime composition, hosting and supervised task APIs."""

from typing import TYPE_CHECKING

from orbit.runtime.models import HostingConfig, HostServer, RuntimeInfo
from orbit.runtime.tasks import RestartPolicy, TaskFailure, TaskInfo, TaskState, TaskSupervisor

if TYPE_CHECKING:
    from orbit.runtime.runtime import Runtime


def __getattr__(name: str) -> object:
    if name == "Runtime":
        from orbit.runtime.runtime import Runtime

        return Runtime
    raise AttributeError(name)


__all__ = [
    "RestartPolicy",
    "HostServer",
    "HostingConfig",
    "Runtime",
    "RuntimeInfo",
    "TaskFailure",
    "TaskInfo",
    "TaskState",
    "TaskSupervisor",
]
