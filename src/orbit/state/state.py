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
"""State facade for consumers that only need read access."""

from orbit.state.models import ApplicationState
from orbit.state.store import StateStore


class State:
    """Expose runtime state while retaining store ownership inside the application."""

    def __init__(self, store: StateStore) -> None:
        """Create a read facade over ``store``."""
        self._store = store

    @property
    def application(self) -> ApplicationState:
        """Return the latest application state."""
        return self._store.current


__all__ = ["State"]
