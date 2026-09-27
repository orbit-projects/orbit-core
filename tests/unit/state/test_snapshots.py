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
"""State validation, identity invariants and optimistic update semantics."""

import pytest
from pydantic import ValidationError

from orbit.state.models import ApplicationState
from orbit.state.store import StateStore
from orbit.types import new_application_id


def test_state_changes_are_validated_and_versioned():
    store = StateStore(ApplicationState(application_id=new_application_id()))
    initial = store.current
    current = store.update(expected_revision=0, service_count=2)
    assert current.revision == 1 and initial.service_count == 0
    with pytest.raises(ValueError):
        store.update(expected_revision=0, service_count=3)
    with pytest.raises(ValidationError):
        store.update(service_count=-1)
    with pytest.raises(ValueError):
        store.update(application_id=new_application_id())
    with pytest.raises(ValueError):
        store.replace(ApplicationState(application_id=new_application_id()))
    replaced = store.replace(store.current.model_copy(update={"service_count": 4}))
    assert replaced.service_count == 4
    assert store.current.service_count == 4

    stale = initial.model_copy(update={"service_count": 5})
    with pytest.raises(ValueError, match="Stale"):
        store.replace(stale)
    assert store.current.service_count == 4


def test_state_transaction_commits_atomically_and_detects_conflicts():
    store = StateStore(ApplicationState(application_id=new_application_id()))
    with store.transaction() as transaction:
        transaction.update(service_count=2, health="healthy")
    assert store.current.revision == 1
    assert store.current.service_count == 2

    pending = store.transaction()
    first = pending.__enter__()
    store.update(service_count=3)
    first.update(service_count=4)
    with pytest.raises(ValueError, match="Stale"):
        pending.__exit__(None, None, None)


def test_state_transaction_rejects_owned_fields_and_closed_mutations():
    store = StateStore(ApplicationState(application_id=new_application_id()))
    manager = store.transaction()
    transaction = manager.__enter__()
    with pytest.raises(ValueError):
        transaction.update(revision=4)
    transaction.rollback()
    with pytest.raises(RuntimeError, match="closed"):
        transaction.update(health="healthy")
    with pytest.raises(RuntimeError, match="closed"):
        transaction.commit()
