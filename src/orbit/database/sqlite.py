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
"""Built-in SQLite SQL adapter, isolated from the event loop by one worker thread."""

from __future__ import annotations

import asyncio
import math
import sqlite3
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import TypeVar, cast
from uuid import UUID

from orbit.database.contracts import SQLExecution, SQLParameters, SQLRow, SQLTransaction, SQLValue
from orbit.errors import DatabaseError

_MAX_ROWS = 10_000
_MAX_SQL_BYTES = 1_000_000
_Result = TypeVar("_Result")


class SQLiteDatabase:
    """Small asynchronous facade over Python's built-in ``sqlite3`` driver.

    All connection access runs on a dedicated single worker, preserving SQLite's thread
    affinity and statement ordering. The connection is opened lazily by the first operation.
    This implementation is appropriate for embedded/local storage; it is not a distributed
    database, connection pool, or substitute for a server database adapter.
    """

    def __init__(self, path: str | Path, *, timeout: float = 5.0) -> None:
        """Configure a SQLite database without blocking on file or connection I/O.

        ``path`` may be a filesystem path or ``":memory:"``. Use one instance per application
        process and register ``aclose`` with the owning container as a resource cleanup.
        """
        if not isinstance(path, (str, Path)) or not str(path):
            raise ValueError("SQLite path must be a non-empty string or Path.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("SQLite timeout must be a finite positive number.")
        if not 0 < timeout <= 300:
            raise ValueError("SQLite timeout must be greater than zero and at most 300 seconds.")
        self._path = str(path)
        self._timeout = float(timeout)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="orbit-sqlite")
        self._lock = asyncio.Lock()
        self._connection: sqlite3.Connection | None = None
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._close_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> SQLiteDatabase:
        """Enter the resource scope without opening SQLite until the first operation."""
        self._ensure_open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the worker-owned connection when its async resource scope ends."""
        await self.aclose()

    async def execute(self, statement: str, parameters: SQLParameters = ()) -> SQLExecution:
        """Run a parameterized write/DDL statement away from the event loop."""
        self._validate_statement(statement)
        parameters = self._normalize_parameters(parameters)
        async with self._lock:
            self._ensure_open()
            return await self._run(self._execute_sync, statement, parameters)

    async def fetch_one(self, statement: str, parameters: SQLParameters = ()) -> SQLRow | None:
        """Fetch one row without exposing the driver's thread-affine cursor."""
        self._validate_statement(statement)
        parameters = self._normalize_parameters(parameters)
        async with self._lock:
            self._ensure_open()
            return await self._run(self._fetch_one_sync, statement, parameters)

    async def fetch_all(
        self, statement: str, parameters: SQLParameters = (), *, limit: int = 1_000
    ) -> tuple[SQLRow, ...]:
        """Fetch a bounded result; raise rather than silently truncate excess rows."""
        self._validate_statement(statement)
        parameters = self._normalize_parameters(parameters)
        self._validate_limit(limit)
        async with self._lock:
            self._ensure_open()
            return await self._run(self._fetch_all_sync, statement, parameters, limit)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[SQLTransaction]:
        """Serialize a transaction and roll it back whenever its body fails or is cancelled."""
        await self._lock.acquire()
        transaction = _SQLiteTransaction(self)
        try:
            self._ensure_open()
            await self._run(self._begin_sync)
            transaction._active = True
            yield transaction
        except BaseException:
            if transaction._active:
                await self._run(self._rollback_sync)
            raise
        else:
            try:
                await self._run(self._commit_sync)
            except BaseException:
                await self._run(self._rollback_sync)
                raise
        finally:
            transaction._active = False
            self._lock.release()

    async def aclose(self) -> None:
        """Close SQLite once; concurrent callers share cancellation-safe cleanup completion."""
        self._bind_loop()
        task = self._close_task
        if task is None:
            self._closed = True
            task = asyncio.create_task(self._finish_close())
            self._close_task = task
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
        task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _finish_close(self) -> None:
        """Drain serialized operations, close SQLite, then stop its dedicated worker."""
        try:
            async with self._lock:
                if self._connection is not None:
                    await self._run(self._close_sync)
        finally:
            await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=False)

    def _ensure_open(self) -> None:
        self._bind_loop()
        if self._closed:
            raise DatabaseError("database.closed", "The database is closed.")

    def _bind_loop(self) -> None:
        """Bind the worker resource to one event loop at first use."""
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise DatabaseError(
                "database.loop-mismatch", "SQLite resource belongs to another event loop."
            )

    async def _run(self, function: Callable[..., _Result], *args: object) -> _Result:
        """Wait for worker completion even if the caller is cancelled.

        Executor work cannot be stopped safely once running. Waiting for it before releasing the
        async lock prevents a cancelled request from overlapping a still-active transaction.
        """
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._executor, function, *args)
        try:
            try:
                return await asyncio.shield(future)
            except sqlite3.IntegrityError as exc:
                if getattr(exc, "sqlite_errorcode", None) in {
                    sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
                    sqlite3.SQLITE_CONSTRAINT_UNIQUE,
                }:
                    raise DatabaseError(
                        "database.constraint-conflict",
                        "Database uniqueness constraint was violated.",
                    ) from None
                raise DatabaseError(
                    "database.operation-failed", "SQLite operation failed."
                ) from None
            except sqlite3.Error:
                raise DatabaseError(
                    "database.operation-failed", "SQLite operation failed."
                ) from None
        except asyncio.CancelledError:
            # Executor work cannot be stopped safely once submitted. Keep the async lock until
            # that work ends, even if cancellation is requested again while draining the call.
            while not future.done():
                try:
                    await asyncio.shield(future)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    # Preserve caller cancellation; this branch only drains the worker safely.
                    break
            if future.done():
                with suppress(BaseException):
                    future.result()
            raise

    def _connect_sync(self) -> sqlite3.Connection:
        if self._connection is None:
            try:
                connection = sqlite3.connect(self._path, timeout=self._timeout)
                connection.execute("PRAGMA foreign_keys = ON")
                self._connection = connection
            except sqlite3.Error:
                raise DatabaseError(
                    "database.connection-failed", "SQLite connection could not be opened."
                ) from None
        return self._connection

    def _execute_sync(self, statement: str, parameters: SQLParameters) -> SQLExecution:
        cursor = self._connect_sync().execute(statement, _sqlite_parameters(parameters))
        return SQLExecution(cursor.rowcount, cursor.lastrowid)

    def _fetch_one_sync(self, statement: str, parameters: SQLParameters) -> SQLRow | None:
        cursor = self._connect_sync().execute(statement, _sqlite_parameters(parameters))
        row = cursor.fetchone()
        return None if row is None else _make_row(cursor, row)

    def _fetch_all_sync(
        self, statement: str, parameters: SQLParameters, limit: int
    ) -> tuple[SQLRow, ...]:
        cursor = self._connect_sync().execute(statement, _sqlite_parameters(parameters))
        rows = cursor.fetchmany(limit + 1)
        if len(rows) > limit:
            raise DatabaseError(
                "database.result-limit", "SQL result exceeds the configured row limit."
            )
        return tuple(_make_row(cursor, row) for row in rows)

    def _begin_sync(self) -> None:
        self._connect_sync().execute("BEGIN")

    def _commit_sync(self) -> None:
        self._connect_sync().commit()

    def _rollback_sync(self) -> None:
        self._connect_sync().rollback()

    def _close_sync(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    @staticmethod
    def _validate_statement(statement: str) -> None:
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError("SQL statement must be a non-empty string.")
        if len(statement.encode("utf-8")) > _MAX_SQL_BYTES:
            raise ValueError("SQL statement exceeds the 1,000,000-byte limit.")

    @staticmethod
    def _normalize_parameters(parameters: SQLParameters) -> SQLParameters:
        if isinstance(parameters, Sequence) and not isinstance(parameters, (str, bytes, bytearray)):
            sequence = tuple(parameters)
            normalized = sequence
        else:
            raise TypeError("SQL parameters must be a positional sequence of scalar values.")
        if len(normalized) > 10_000:
            raise ValueError("SQL parameter count cannot exceed 10,000.")
        for value in normalized:
            if value is not None and not isinstance(
                value, (bool, int, float, str, bytes, date, datetime, time, Decimal, UUID)
            ):
                raise TypeError("SQL parameters must use supported scalar values.")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("SQL floating-point parameters must be finite.")
            if isinstance(value, Decimal) and not value.is_finite():
                raise ValueError("SQL decimal parameters must be finite.")
        return normalized

    @staticmethod
    def _validate_limit(limit: int) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_ROWS:
            raise ValueError("SQL result limit must be between 1 and 10,000 rows.")


class _SQLiteTransaction:
    """Task-bound transaction facade that bypasses the database lock it already owns."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database
        self._owner = asyncio.current_task()
        self._active = False

    async def execute(self, statement: str, parameters: SQLParameters = ()) -> SQLExecution:
        """Execute within the active task-owned SQLite transaction."""
        self._check()
        self._database._validate_statement(statement)
        parameters = self._database._normalize_parameters(parameters)
        return await self._database._run(self._database._execute_sync, statement, parameters)

    async def fetch_one(self, statement: str, parameters: SQLParameters = ()) -> SQLRow | None:
        """Fetch one row within the active task-owned SQLite transaction."""
        self._check()
        self._database._validate_statement(statement)
        parameters = self._database._normalize_parameters(parameters)
        return await self._database._run(self._database._fetch_one_sync, statement, parameters)

    async def fetch_all(
        self, statement: str, parameters: SQLParameters = (), *, limit: int = 1_000
    ) -> tuple[SQLRow, ...]:
        """Fetch a bounded result within the active task-owned SQLite transaction."""
        self._check()
        self._database._validate_statement(statement)
        parameters = self._database._normalize_parameters(parameters)
        self._database._validate_limit(limit)
        return await self._database._run(
            self._database._fetch_all_sync, statement, parameters, limit
        )

    def _check(self) -> None:
        if not self._active or asyncio.current_task() is not self._owner:
            raise DatabaseError(
                "database.transaction-scope", "Transaction is inactive or used by another task."
            )


def _sqlite_parameters(parameters: SQLParameters) -> Mapping[str, object]:
    """Convert accepted immutable protocol parameters to sqlite3's binding shape."""
    # Core SQL uses PostgreSQL-style numbered parameters ($1, $2, ...). SQLite also supports
    # these named placeholders; pass a mapping keyed by the name without `$` to avoid Python
    # 3.14's deprecated sequence binding for named placeholders.
    return {str(index): _sqlite_value(value) for index, value in enumerate(parameters, start=1)}


def _sqlite_value(value: SQLValue) -> object:
    """Encode portable extended scalars as text for SQLite's dynamic type system."""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return bytes(value)
    return value


def _make_row(cursor: sqlite3.Cursor, values: Sequence[object]) -> SQLRow:
    """Detach a worker-owned sqlite row and reject duplicate/unsupported result values."""
    names = tuple(column[0] for column in cursor.description or ())
    detached = tuple(cast(SQLValue, value) for value in values)
    return SQLRow(names, detached)


__all__ = ["SQLiteDatabase"]
