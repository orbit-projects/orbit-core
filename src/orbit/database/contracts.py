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
"""Typed, provider-neutral contracts for asynchronous SQL access."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Protocol, TypeAlias, runtime_checkable
from uuid import UUID

SQLValue: TypeAlias = (
    None | bool | int | float | str | bytes | date | datetime | time | Decimal | UUID
)
"""Portable SQL values supported by Core and database adapter contracts."""

SQLParameters: TypeAlias = tuple[SQLValue, ...]
"""Positional bound values; cross-adapter queries use PostgreSQL-style ``$1`` placeholders."""


@dataclass(frozen=True, slots=True)
class SQLRow(Mapping[str, SQLValue]):
    """Detached immutable result row addressable by column name."""

    _columns: tuple[str, ...]
    _values: tuple[SQLValue, ...]

    def __post_init__(self) -> None:
        """Reject malformed adapter output at the shared SQL boundary."""
        columns = tuple(self._columns)
        values = tuple(self._values)
        object.__setattr__(self, "_columns", columns)
        object.__setattr__(self, "_values", values)
        if len(columns) != len(values):
            raise ValueError("SQL row columns and values must have matching lengths.")
        if len(columns) > 10_000:
            raise ValueError("SQL rows cannot contain more than 10,000 columns.")
        if any(
            not isinstance(column, str)
            or not column
            or len(column) > 255
            or any(ord(character) < 32 or ord(character) == 127 for character in column)
            for column in columns
        ):
            raise ValueError("SQL result column names must be non-empty strings.")
        if len(set(columns)) != len(columns):
            raise ValueError("SQL result column names must be unique.")
        if any(
            value is not None
            and not isinstance(
                value, (bool, int, float, str, bytes, date, datetime, time, Decimal, UUID)
            )
            for value in values
        ):
            raise TypeError("SQL row values must use supported portable scalar types.")
        if any(isinstance(value, Decimal) and not value.is_finite() for value in values):
            raise ValueError("SQL decimal values must be finite.")
        if any(isinstance(value, float) and not math.isfinite(value) for value in values):
            raise ValueError("SQL floating-point values must be finite.")

    def __getitem__(self, key: str) -> SQLValue:
        """Return the value for a result column or raise ``KeyError``."""
        try:
            return self._values[self._columns.index(key)]
        except ValueError as exc:
            raise KeyError(key) from exc

    def __iter__(self) -> Iterator[str]:
        """Iterate result column names in database-returned order."""
        return iter(self._columns)

    def __len__(self) -> int:
        """Return the number of columns in this row."""
        return len(self._columns)


@dataclass(frozen=True, slots=True)
class SQLExecution:
    """Result metadata for a statement that does not return selected rows."""

    rowcount: int
    lastrowid: int | None = None

    def __post_init__(self) -> None:
        """Validate result metadata supplied by a database adapter."""
        if isinstance(self.rowcount, bool) or not isinstance(self.rowcount, int):
            raise TypeError("SQL rowcount must be an integer.")
        if self.rowcount < -1:
            raise ValueError("SQL rowcount cannot be less than -1.")
        if self.lastrowid is not None and (
            isinstance(self.lastrowid, bool) or not isinstance(self.lastrowid, int)
        ):
            raise TypeError("SQL lastrowid must be an integer or None.")


@runtime_checkable
class SQLTransaction(Protocol):
    """Connection-pinned SQL operations valid only within one transaction scope."""

    async def execute(self, statement: str, parameters: SQLParameters = ()) -> SQLExecution:
        """Execute one parameterized statement inside the active transaction."""

    async def fetch_one(self, statement: str, parameters: SQLParameters = ()) -> SQLRow | None:
        """Return one detached row, or ``None`` when there is no result."""

    async def fetch_all(
        self, statement: str, parameters: SQLParameters = (), *, limit: int = 1_000
    ) -> tuple[SQLRow, ...]:
        """Return at most ``limit`` rows; adapters reject results exceeding the bound."""


@runtime_checkable
class SQLDatabase(Protocol):
    """Common asynchronous SQL capability implemented by Core and SQL adapters."""

    async def execute(self, statement: str, parameters: SQLParameters = ()) -> SQLExecution:
        """Execute a parameterized statement and return affected-row metadata."""

    async def fetch_one(self, statement: str, parameters: SQLParameters = ()) -> SQLRow | None:
        """Return one detached row, or ``None`` when there is no result."""

    async def fetch_all(
        self, statement: str, parameters: SQLParameters = (), *, limit: int = 1_000
    ) -> tuple[SQLRow, ...]:
        """Return a bounded tuple of detached rows."""

    def transaction(self) -> AbstractAsyncContextManager[SQLTransaction]:
        """Create a transaction context that commits on success and rolls back on failure."""

    async def aclose(self) -> None:
        """Close the database and release all owned resources."""


__all__ = ["SQLDatabase", "SQLExecution", "SQLParameters", "SQLRow", "SQLTransaction", "SQLValue"]
