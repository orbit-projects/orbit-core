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
"""Contract tests for Core's asynchronous SQLite implementation."""

import asyncio
import threading
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from orbit.container import Container
from orbit.database import SQLDatabase, SQLExecution, SQLiteDatabase, SQLParameters, SQLRow
from orbit.errors import DatabaseError


@pytest.fixture
async def database(tmp_path):
    """Give each test an isolated on-disk database and deterministic cleanup."""
    instance = SQLiteDatabase(tmp_path / "test.sqlite")
    try:
        yield instance
    finally:
        await instance.aclose()


async def test_sqlite_implements_shared_contract_and_parameterized_rows(database) -> None:
    """SQL adapters expose typed detached rows and bound values through one contract."""
    assert isinstance(database, SQLDatabase)
    await database.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    result = await database.execute("INSERT INTO items (name) VALUES ($1)", ("Orbit",))

    row = await database.fetch_one("SELECT id, name FROM items WHERE id = $1", (result.lastrowid,))
    rows = await database.fetch_all("SELECT id, name FROM items")

    assert result.rowcount == 1
    assert row is not None
    assert row["name"] == "Orbit"
    assert len(rows) == 1


async def test_container_resource_scope_closes_sqlite_database(tmp_path) -> None:
    """Container ownership closes a directly registered SQLite capability on shutdown."""
    container = Container()
    container.register_resource(SQLDatabase, lambda _: SQLiteDatabase(tmp_path / "managed.sqlite"))

    database = await container.aresolve(SQLDatabase)
    await database.execute("CREATE TABLE values_table (value INTEGER)")
    await container.aclose()

    with pytest.raises(DatabaseError) as caught:
        await database.execute("SELECT 1")
    assert caught.value.problem.code == "database.closed"


async def test_concurrent_close_callers_wait_for_the_same_cleanup(tmp_path, monkeypatch) -> None:
    """Every aclose caller waits until the shared worker and connection have shut down."""
    database = SQLiteDatabase(tmp_path / "concurrent-close.sqlite")
    await database.execute("SELECT 1")
    close_started = threading.Event()
    release_close = threading.Event()
    original_close = database._close_sync

    def delayed_close() -> None:
        close_started.set()
        if not release_close.wait(timeout=5):
            raise TimeoutError("Test did not release the SQLite close worker.")
        original_close()

    monkeypatch.setattr(database, "_close_sync", delayed_close)
    first = asyncio.create_task(database.aclose())
    second: asyncio.Task[None] | None = None
    cancelled = False
    try:
        assert await asyncio.to_thread(close_started.wait, 2)
        second = asyncio.create_task(database.aclose())
        await asyncio.sleep(0.01)
        assert not first.done()
        assert not second.done()
        first.cancel()
        cancelled = True
        await asyncio.sleep(0)
        assert not first.done()
        assert not second.done()
    finally:
        release_close.set()
        if cancelled:
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            await first
        if second is not None:
            await second


async def test_sqlite_binds_numbered_parameters_by_name_without_order_assumptions(database) -> None:
    """SQLite follows Core's numbered placeholder contract, including repeated/out-of-order use."""
    row = await database.fetch_one("SELECT $2 AS second, $1 AS first, $2 AS repeated", ("a", "b"))

    assert row is not None
    assert (row["first"], row["second"], row["repeated"]) == ("a", "b", "b")


async def test_sqlite_encodes_portable_extended_values_as_text(database) -> None:
    """SQLite preserves standard scalar values through documented lossless text encodings."""
    await database.execute("CREATE TABLE values_table (value TEXT NOT NULL)")
    values = (
        Decimal("12.340"),
        date(2026, 10, 4),
        datetime(2026, 10, 4, 12, 30, tzinfo=UTC),
        UUID("12345678-1234-5678-1234-567812345678"),
    )
    for value in values:
        await database.execute("INSERT INTO values_table VALUES ($1)", (value,))

    rows = await database.fetch_all("SELECT value FROM values_table")

    assert [row["value"] for row in rows] == [
        "12.340",
        "2026-10-04",
        "2026-10-04T12:30:00+00:00",
        "12345678-1234-5678-1234-567812345678",
    ]


async def test_transaction_commits_and_rolls_back_atomically(database) -> None:
    """A context success commits while any raised exception rolls back its writes."""
    await database.execute("CREATE TABLE values_table (value INTEGER NOT NULL)")
    async with database.transaction() as transaction:
        await transaction.execute("INSERT INTO values_table VALUES ($1)", (1,))

    with pytest.raises(ValueError):
        async with database.transaction() as transaction:
            await transaction.execute("INSERT INTO values_table VALUES ($1)", (2,))
            raise ValueError("abort")

    rows = await database.fetch_all("SELECT value FROM values_table")
    assert [row["value"] for row in rows] == [1]


async def test_cancelled_inflight_worker_operation_rolls_back_before_unlocking(
    database, monkeypatch
) -> None:
    """Cancellation waits for SQLite worker completion, rolls back, then releases other tasks."""
    await database.execute("CREATE TABLE values_table (value INTEGER NOT NULL)")
    worker_started = threading.Event()
    release_worker = threading.Event()
    original_execute = database._execute_sync

    def delayed_execute(statement: str, parameters: SQLParameters) -> SQLExecution:
        if statement == "INSERT INTO values_table VALUES ($1)" and parameters == (1,):
            worker_started.set()
            if not release_worker.wait(timeout=5):
                raise TimeoutError("Test did not release the SQLite worker.")
        return original_execute(statement, parameters)

    monkeypatch.setattr(database, "_execute_sync", delayed_execute)

    async def transaction_work() -> None:
        async with database.transaction() as transaction:
            await transaction.execute("INSERT INTO values_table VALUES ($1)", (1,))

    owner = asyncio.create_task(transaction_work())
    writer: asyncio.Task[SQLExecution] | None = None
    try:
        assert await asyncio.to_thread(worker_started.wait, 2)
        owner.cancel()
        writer = asyncio.create_task(database.execute("INSERT INTO values_table VALUES ($1)", (2,)))
        await asyncio.sleep(0.01)
        owner.cancel()
        await asyncio.sleep(0)
        assert not owner.done()
        assert not writer.done()
    finally:
        release_worker.set()

    with pytest.raises(asyncio.CancelledError):
        await owner
    assert writer is not None
    await writer
    rows = await database.fetch_all("SELECT value FROM values_table")
    assert [row["value"] for row in rows] == [2]


async def test_transaction_holds_connection_against_interleaving_tasks(database) -> None:
    """Independent tasks cannot accidentally execute inside another task's transaction."""
    await database.execute("CREATE TABLE values_table (value INTEGER NOT NULL)")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def transaction_work() -> None:
        async with database.transaction() as transaction:
            await transaction.execute("INSERT INTO values_table VALUES ($1)", (1,))
            entered.set()
            await release.wait()

    async def independent_write() -> None:
        await entered.wait()
        await database.execute("INSERT INTO values_table VALUES ($1)", (2,))

    owner = asyncio.create_task(transaction_work())
    writer = asyncio.create_task(independent_write())
    await entered.wait()
    await asyncio.sleep(0)
    assert not writer.done()
    release.set()
    await asyncio.gather(owner, writer)

    rows = await database.fetch_all("SELECT value FROM values_table ORDER BY value")
    assert [row["value"] for row in rows] == [1, 2]


async def test_fetch_all_rejects_results_over_explicit_limit(database) -> None:
    """Result bounds fail explicitly instead of returning a misleading partial result."""
    await database.execute("CREATE TABLE values_table (value INTEGER NOT NULL)")
    await database.execute("INSERT INTO values_table VALUES (1), (2)")

    with pytest.raises(DatabaseError) as caught:
        await database.fetch_all("SELECT value FROM values_table", limit=1)

    assert caught.value.problem.code == "database.result-limit"
    assert "SELECT" not in caught.value.problem.message


async def test_database_failures_do_not_include_sql_or_driver_details(database) -> None:
    """Diagnostics use a stable safe message and do not retain raw statements."""
    statement = "SELECT private_customer_data FROM missing_table"

    with pytest.raises(DatabaseError) as caught:
        await database.fetch_one(statement)

    assert caught.value.problem.code == "database.operation-failed"
    assert statement not in str(caught.value)
    assert caught.value.__cause__ is None


async def test_sqlite_unique_constraint_has_a_provider_neutral_error_code(database) -> None:
    """Unique conflicts are distinguishable without exposing SQLite exception details."""
    await database.execute("CREATE TABLE unique_items (id INTEGER PRIMARY KEY, slug TEXT UNIQUE)")
    await database.execute("INSERT INTO unique_items (id, slug) VALUES ($1, $2)", (1, "taken"))

    with pytest.raises(DatabaseError) as caught:
        await database.execute("INSERT INTO unique_items (id, slug) VALUES ($1, $2)", (2, "taken"))

    assert caught.value.problem.code == "database.constraint-conflict"
    assert caught.value.problem.message == "Database uniqueness constraint was violated."
    assert "UNIQUE" not in str(caught.value)


async def test_sqlite_non_unique_integrity_failures_remain_operation_errors(database) -> None:
    """NOT NULL and other non-unique constraints are not mislabeled as key conflicts."""
    await database.execute("CREATE TABLE required_items (value TEXT NOT NULL)")

    with pytest.raises(DatabaseError) as caught:
        await database.execute("INSERT INTO required_items (value) VALUES ($1)", (None,))

    assert caught.value.problem.code == "database.operation-failed"


async def test_transaction_is_task_bound(database) -> None:
    """A transaction object cannot be passed to another coroutine to bypass serialization."""
    async with database.transaction() as transaction:

        async def misuse() -> None:
            await transaction.execute("SELECT 1")

        with pytest.raises(DatabaseError) as caught:
            await asyncio.create_task(misuse())

    assert caught.value.problem.code == "database.transaction-scope"


async def test_close_rejects_later_operations(tmp_path) -> None:
    """Closing releases the worker and makes the resource's terminal state explicit."""
    database = SQLiteDatabase(tmp_path / "close.sqlite")
    await database.execute("SELECT 1")
    await database.aclose()

    with pytest.raises(DatabaseError) as caught:
        await database.execute("SELECT 1")

    assert caught.value.problem.code == "database.closed"


async def test_sqlite_is_bound_to_one_event_loop(database) -> None:
    """Loop-bound locks and worker ownership reject resource reuse on another asyncio loop."""
    await database.execute("SELECT 1")

    async def use_from_another_loop() -> None:
        with pytest.raises(DatabaseError) as caught:
            await asyncio.to_thread(asyncio.run, database.fetch_one("SELECT 1"))
        assert caught.value.problem.code == "database.loop-mismatch"

    await use_from_another_loop()
    assert await database.fetch_one("SELECT 1") is not None


def test_sql_rows_validate_adapter_values_and_column_metadata() -> None:
    """The shared adapter boundary rejects malformed names and nonportable mutable values."""
    with pytest.raises(TypeError):
        SQLRow(("value",), ([],))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        SQLRow(("duplicate", "duplicate"), (1, 2))


@pytest.mark.parametrize("limit", [0, -1, 10_001, True])
async def test_fetch_all_requires_a_bounded_positive_limit(database, limit) -> None:
    """Invalid limits are rejected before sending work to SQLite's worker thread."""
    with pytest.raises(ValueError):
        await database.fetch_all("SELECT 1", limit=limit)


async def test_statement_rejects_empty_and_oversized_sql(database) -> None:
    """Statement length is bounded before it reaches the SQLite driver."""
    with pytest.raises(ValueError):
        await database.execute("  ")
    with pytest.raises(ValueError):
        await database.execute("x" * 1_000_001)
