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
"""Validated, versioned state replacement under a thread lock."""

from threading import RLock

from orbit.state.models import ApplicationState


class StateStore:
    """Store immutable snapshots with identity protection and optimistic revision checks."""

    def __init__(self, initial: ApplicationState) -> None:
        self._state = initial.model_copy(deep=True)
        self._lock = RLock()

    @property
    def current(self) -> ApplicationState:
        """Read a detached snapshot."""
        with self._lock:
            return self._state.model_copy(deep=True)

    def update(
        self, *, expected_revision: int | None = None, **changes: object
    ) -> ApplicationState:
        """Validate changes and increment revision atomically; reject stale writes."""
        with self._lock:
            if expected_revision is not None and expected_revision != self._state.revision:
                raise ValueError("Stale state revision.")
            if "application_id" in changes or "revision" in changes:
                raise ValueError("Identity and revision are managed by StateStore.")
            state = ApplicationState.model_validate(
                {**self._state.model_dump(), **changes, "revision": self._state.revision + 1}
            )
            self._state = state
            return self.current

    def replace(self, state: ApplicationState) -> ApplicationState:
        """Replace the snapshot without changing its application identity."""
        if state.application_id != self._state.application_id:
            raise ValueError("Cannot replace state for a different application.")
        return self.update(**state.model_dump(exclude={"application_id", "revision"}))


__all__ = ["StateStore"]
