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

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
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
        """Replace a matching snapshot without allowing stale state to overwrite newer data."""
        if state.application_id != self._state.application_id:
            raise ValueError("Cannot replace state for a different application.")
        return self.update(
            expected_revision=state.revision,
            **state.model_dump(exclude={"application_id", "revision"}),
        )

    @contextmanager
    def transaction(self) -> Iterator[StateTransaction]:
        """Stage several updates and commit them as one optimistic revision."""
        with self._lock:
            transaction = StateTransaction(self, self._state.revision)
        try:
            yield transaction
        except BaseException:
            transaction.rollback()
            raise
        else:
            transaction.commit()


class StateTransaction:
    """Optimistic state mutation that validates and commits atomically."""

    def __init__(self, store: StateStore, revision: int) -> None:
        self._store = store
        self._revision = revision
        self._changes: dict[str, object] = {}
        self._closed = False

    def update(self, **changes: object) -> StateTransaction:
        """Stage field changes; identity and revision remain store-owned."""
        if self._closed:
            raise RuntimeError("State transaction is closed.")
        if "application_id" in changes or "revision" in changes:
            raise ValueError("Identity and revision are managed by StateStore.")
        self._changes.update(changes)
        return self

    def commit(self) -> ApplicationState:
        """Validate and atomically commit staged changes exactly once."""
        if self._closed:
            raise RuntimeError("State transaction is closed.")
        self._closed = True
        return self._store.update(expected_revision=self._revision, **self._changes)

    def rollback(self) -> None:
        """Discard staged changes without modifying the store."""
        self._closed = True


__all__ = ["StateStore", "StateTransaction"]
