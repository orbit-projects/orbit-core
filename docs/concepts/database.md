# Database capability

Orbit Core defines one asynchronous SQL capability and includes a small SQLite implementation.
The capability keeps application code independent of a specific server database adapter; Redis
and other key-value systems are separate capabilities rather than being forced into SQL's model.

## SQLite baseline

`SQLiteDatabase` uses Python's standard-library `sqlite3` module. It creates a dedicated worker
thread lazily, and all access to its connection is serialized on that worker so blocking database
work does not run on the event loop. Use one instance per application process and close it through
the owner that created it:

```python
from orbit.database import SQLiteDatabase

database = SQLiteDatabase("var/orbit.db")
await database.execute(
    "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)"
)
await database.execute("INSERT INTO notes (body) VALUES ($1)", ("hello",))
notes = await database.fetch_all("SELECT id, body FROM notes", limit=100)
await database.aclose()
```

Always bind values through the `parameters` argument. SQL text is not interpolated by Orbit.
Use positional `$1`, `$2`, … placeholders and supply values in order for SQL portable across SQLite
and PostgreSQL. `fetch_all` requires a row limit from 1 to 10,000 and raises a structured
`DatabaseError` rather than silently truncating a larger result. Rows are detached immutable
mappings. Driver exception text and SQL statements are not copied into the public database error
message.
Unique and primary-key violations have the stable problem code
`database.constraint-conflict`; other integrity failures remain generic operation errors. The
`orbit-sql` capability translates that code to `orbit_data.RepositoryConflictError` for repository
`add` and `update`, keeping applications independent of SQLite and asyncpg exception types. It also
exports `SQL_DATABASE_KEY` for resolving the active `SQLDatabase` through Core's container. The
optional `PostgresPlugin` from `orbit-sql-postgres` registers a lazy, Core-managed pool under that
key; `SQLAdapterRegistry` remains available when an application prefers to construct and own the
adapter explicitly. See
[ADR 0008](../architecture/adr/0008-sql-repository-conflicts.md).
The shared value contract includes booleans, integers, finite floats, text, bytes, finite decimals,
dates/times, and UUIDs. SQLite encodes decimals, dates/times, and UUIDs as text because SQLite has
dynamic storage classes; reads therefore return those columns as strings unless the application
explicitly converts them.

Transactions hold the connection's async serialization lock until commit or rollback. This
prevents an unrelated task using the same instance from accidentally running statements inside a
transaction owned by another task:

```python
async with database.transaction() as transaction:
    await transaction.execute("INSERT INTO notes (body) VALUES ($1)", ("atomic",))
```

An exception or cancellation in the context rolls back its transaction. Transaction objects are
bound to the task that opened them and must not be passed to another task. `SQLiteDatabase` is also
an async context manager, so the Core container can own its full lifetime:

If a transaction task is cancelled repeatedly while SQLite work is running in the worker thread,
it waits for that operation to finish before rolling back and releasing the connection lock.

```python
from orbit.container import Container
from orbit.database import SQLDatabase, SQLiteDatabase

container = Container()
container.register_resource(SQLDatabase, lambda _: SQLiteDatabase("var/orbit.db"))
database = await container.aresolve(SQLDatabase)
await database.execute("SELECT 1")
await container.aclose()  # invokes SQLiteDatabase.__aexit__ and closes its worker
```

Concurrent `aclose()` calls share the same shutdown operation. Cancellation of one waiter does not
cancel connection or worker cleanup; that caller receives `CancelledError` only after cleanup ends.

## Adapter boundary and limitations

The shared `SQLDatabase` protocol covers parameterized execute, one-row and bounded multi-row
reads, transactions, portable positional placeholders, and async close. Database-specific DDL,
isolation levels, error mapping, and capabilities beyond this contract remain adapter-specific. A
server database adapter should implement this contract and document its pool, retry, transaction,
and shutdown semantics. No server database SDK or connection pool is installed by Core.

SQLite is a local embedded database, not a distributed coordination service or a substitute for a
server database in every deployment. Orbit does not enable WAL, create directories, run migrations,
or make durability promises on behalf of the filesystem. Configure and back up its file according
to the deployment's requirements.

Schema migration support is the separate optional `orbit-migrations` package, not a Core startup
feature. It accepts explicit async callbacks and records each successful version transactionally.
Run it as a single deployment step before serving traffic; it is forward-only and does not provide a
distributed migration lock or automatic module discovery.
