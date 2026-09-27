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
"""Typed runtime state APIs."""

from orbit.state.coordination import InMemoryStateCoordinator, Lease, StateCoordinator
from orbit.state.models import ApplicationState
from orbit.state.namespace import NamespaceTransaction, StateEntry, StateNamespace
from orbit.state.provider import InMemoryStateProvider, StateProvider
from orbit.state.state import State
from orbit.state.store import StateStore, StateTransaction

__all__ = [
    "ApplicationState",
    "InMemoryStateCoordinator",
    "Lease",
    "State",
    "NamespaceTransaction",
    "StateEntry",
    "StateNamespace",
    "StateProvider",
    "InMemoryStateProvider",
    "StateCoordinator",
    "StateStore",
    "StateTransaction",
]
