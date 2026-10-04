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
"""Verify namespaced state isolation, expiry, capacity, and optimistic transactions."""

import time

import pytest

from orbit.state import StateEntry, StateNamespace, StateProvider


def test_namespace_isolates_values_and_supports_ttl_and_cas():
    namespace = StateNamespace("sessions", max_entries=2)
    with pytest.raises(AttributeError):
        namespace.name = "other"  # type: ignore[misc]
    version = namespace.set("user:1", {"roles": ["reader"]})
    value = namespace.get("user:1")
    value["roles"].append("mutated")
    assert namespace.get("user:1") == {"roles": ["reader"]}
    with pytest.raises(ValueError, match="Stale"):
        namespace.set("user:1", {}, expected_version=version + 1)
    namespace.set("temporary", True, ttl=0.001)
    time.sleep(0.01)
    assert namespace.get("temporary") is None
    assert namespace.delete("user:1", expected_version=version)


@pytest.mark.parametrize(
    "ttl", [0, -1, True, "1", object(), float("inf"), float("-inf"), float("nan")]
)
def test_namespace_rejects_non_finite_or_non_positive_ttl(ttl: float) -> None:
    namespace = StateNamespace("ttl-validation")
    with pytest.raises(ValueError, match="ttl must be positive"):
        namespace.set("value", 1, ttl=ttl)
    with (
        namespace.transaction() as transaction,
        pytest.raises(ValueError, match="ttl must be positive"),
    ):
        transaction.set("value", 1, ttl=ttl)


@pytest.mark.parametrize("name", ["", None, 42, "UpperCase", "with space"])
def test_namespace_rejects_invalid_names(name: object) -> None:
    with pytest.raises(ValueError, match="Namespace names"):
        StateNamespace(name)  # type: ignore[arg-type]


def test_namespace_capacity_and_snapshot():
    namespace = StateNamespace("cache", max_entries=1)
    namespace.set("a", 1)
    with pytest.raises(RuntimeError):
        namespace.set("b", 2)
    assert namespace.snapshot()[0].key == "a"


def test_namespace_expiry_index_ignores_refreshed_deadlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An older TTL record must not remove a key refreshed with a later deadline."""
    import orbit.state.namespace as namespace_module

    now = [10.0]
    monkeypatch.setattr(namespace_module, "monotonic", lambda: now[0])
    namespace = StateNamespace("expiry-index")
    namespace.set("session", "old", ttl=5)
    now[0] = 11.0
    namespace.set("session", "refreshed", ttl=10)

    now[0] = 15.0
    assert namespace.get("session") == "refreshed"
    now[0] = 21.0
    assert namespace.get("session") is None


def test_namespace_transaction_expiry_index_tracks_refreshes_and_batches_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transactional refreshes ignore stale deadlines and batch expiry into one revision."""
    import orbit.state.namespace as namespace_module

    now = [10.0]
    monkeypatch.setattr(namespace_module, "monotonic", lambda: now[0])
    namespace = StateNamespace("transaction-expiry")
    namespace.set("session", "old", ttl=5)

    now[0] = 11.0
    with namespace.transaction() as transaction:
        transaction.set("session", "refreshed", ttl=10)
        transaction.set("lease", "active", ttl=10)
    assert namespace.revision == 2

    now[0] = 15.0
    assert namespace.get("session") == "refreshed"
    assert namespace.get("lease") == "active"

    now[0] = 21.0
    assert namespace.revision == 3
    assert namespace.snapshot() == ()


def test_transaction_ttl_starts_at_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long staging interval must not consume a value's post-commit lifetime."""
    import orbit.state.namespace as namespace_module

    now = [10.0]
    monkeypatch.setattr(namespace_module, "monotonic", lambda: now[0])
    namespace = StateNamespace("transaction-ttl-start")
    manager = namespace.transaction()
    transaction = manager.__enter__()
    transaction.set("session", "active", ttl=5)

    now[0] = 100.0
    manager.__exit__(None, None, None)
    now[0] = 104.9
    assert namespace.get("session") == "active"
    now[0] = 105.0
    assert namespace.get("session") is None


def test_namespace_expiry_index_compacts_superseded_ttls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated TTL refreshes cannot grow the stale-deadline index without bound."""
    import orbit.state.namespace as namespace_module

    monkeypatch.setattr(namespace_module, "monotonic", lambda: 10.0)
    namespace = StateNamespace("expiry-compaction", max_entries=1)
    for value in range(200):
        namespace.set("session", value, ttl=60)

    assert namespace.get("session") == 199
    assert len(namespace._expiry_heap) <= 64
    assert len(namespace._expiry_tokens) == 1


def test_state_entry_validates_metadata_and_detaches_value() -> None:
    original = {"roles": ["reader"]}
    entry = StateEntry("user:1", original, 1, expires_at=10)

    original["roles"].append("writer")
    assert entry.value == {"roles": ["reader"]}
    assert entry.expires_at == 10.0

    with pytest.raises(ValueError, match="State keys"):
        StateEntry("bad key", None, 1)
    with pytest.raises(ValueError, match="positive"):
        StateEntry("valid", None, 0)
    with pytest.raises(ValueError, match="finite"):
        StateEntry("valid", None, 1, expires_at=float("nan"))


def test_namespace_rejects_boolean_capacity() -> None:
    with pytest.raises(ValueError, match="positive"):
        StateNamespace("cache", max_entries=True)
    with pytest.raises(ValueError, match="positive"):
        StateNamespace("cache", max_entries="1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="1,000,000"):
        StateNamespace("cache", max_entries=1_000_001)


def test_state_provider_is_runtime_checkable_contract():
    class Backend:
        async def get(self, namespace, key, default=None):
            return default

        async def set(self, namespace, key, value, *, ttl=None, expected_version=None):
            return 1

        async def delete(self, namespace, key, *, expected_version=None):
            return False

        async def close(self):
            return None

    assert isinstance(Backend(), StateProvider)


def test_namespace_transaction_commits_atomically_and_detects_conflicts():
    namespace = StateNamespace("atomic")
    with namespace.transaction() as transaction:
        transaction.set("first", 1).set("second", 2)
        assert transaction.get("first") == 1
    entries = {entry.key: entry for entry in namespace.snapshot()}
    assert entries["first"].value == 1
    assert entries["first"].version == entries["second"].version

    transaction = namespace.transaction()
    first = transaction.__enter__()
    namespace.set("outside", True)
    first.set("stale", True)
    with pytest.raises(ValueError, match="Stale"):
        transaction.__exit__(None, None, None)


def test_transaction_delete_of_missing_key_is_a_revision_noop() -> None:
    """No-op deletes must not invalidate unrelated optimistic transactions."""
    namespace = StateNamespace("no-op-delete")
    with namespace.transaction() as transaction:
        transaction.delete("missing")
    assert namespace.revision == 0
    assert namespace.snapshot() == ()


def test_namespace_validates_names_keys_and_transaction_lifecycle():
    with pytest.raises(ValueError):
        StateNamespace("Bad Name")
    namespace = StateNamespace("lifecycle", max_entries=2)
    with pytest.raises(ValueError):
        namespace.set("bad key", 1)
    with pytest.raises(ValueError, match="expected_version"):
        namespace.set("ok", 1, expected_version=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="expected_version"):
        namespace.delete("ok", expected_version=-1)
    with pytest.raises(ValueError):
        namespace.set("ok", 1, ttl=0)
    assert namespace.delete("missing") is False
    with namespace.transaction() as transaction:
        transaction.set("kept", {"value": 1})
        transaction.delete("missing")
    assert namespace.get("kept") == {"value": 1}
    manager = namespace.transaction()
    transaction = manager.__enter__()
    manager.__exit__(ValueError, ValueError("abort"), None)
    with pytest.raises(RuntimeError, match="closed"):
        transaction.get("kept")


def test_namespace_transaction_change_capacity_is_bounded(monkeypatch):
    """A transaction cannot stage an unbounded set of distinct changes."""
    monkeypatch.setattr("orbit.state.namespace._MAX_CORE_CAPACITY", 1)
    namespace = StateNamespace("capacity", max_entries=1)
    with pytest.raises(RuntimeError, match="capacity"), namespace.transaction() as transaction:
        transaction.set("first", 1).set("second", 2)
    assert namespace.snapshot() == ()


def test_namespace_value_detachment_failures_do_not_mutate_state() -> None:
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise RuntimeError("cannot copy")

    class FailsOnSecondCopy:
        def __init__(self):
            self.calls = 0

        def __deepcopy__(self, memo):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("cannot copy")
            return self

    namespace = StateNamespace("copy-failure")
    with pytest.raises(RuntimeError, match="cannot copy"):
        namespace.set("value", Uncopyable())
    assert namespace.revision == 0
    assert namespace.snapshot() == ()

    namespace.set("stable", "before")
    manager = namespace.transaction()
    transaction = manager.__enter__()
    transaction.set("good", "not committed").set("bad", FailsOnSecondCopy())
    with pytest.raises(RuntimeError, match="cannot copy"):
        manager.__exit__(None, None, None)
    assert namespace.get("stable") == "before"
    assert namespace.get("good") is None
    assert namespace.revision == 1


def test_namespace_set_prepares_entry_before_publishing_revision() -> None:
    """A value is detached once, before a successful write becomes observable."""

    class CountsCopies:
        def __init__(self) -> None:
            self.calls = 0

        def __deepcopy__(self, memo):
            self.calls += 1
            return self

    value = CountsCopies()
    namespace = StateNamespace("single-copy")
    assert namespace.set("value", value) == 1
    assert value.calls == 1
    assert namespace.revision == 1


def test_expiry_advances_revision_and_invalidates_existing_transactions() -> None:
    namespace = StateNamespace("expiry")
    namespace.set("temporary", True, ttl=0.001)
    manager = namespace.transaction()
    transaction = manager.__enter__()
    time.sleep(0.01)
    assert namespace.get("temporary") is None
    transaction.set("after", True)
    with pytest.raises(ValueError, match="Stale"):
        manager.__exit__(None, None, None)
