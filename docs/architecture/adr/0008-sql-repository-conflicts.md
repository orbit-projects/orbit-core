# ADR 0008: Provider-neutral SQL repository conflicts

- Status: Accepted
- Date: 2026-10-04

## Context

`orbit-data` documents a stable conflict exception for writes that violate uniqueness constraints,
but Core SQLite and the PostgreSQL adapter previously reduced all driver failures to a generic
database-operation error. That made repository callers either depend on provider exceptions or lose
the distinction entirely, contradicting the capability boundary.

## Decision

Core SQL implementations identify unique and primary-key constraint violations with the stable
`DatabaseError` problem code `database.constraint-conflict` and a provider-neutral message. Core's
SQLite implementation recognizes only SQLite's unique and primary-key extended result codes. The
PostgreSQL adapter recognizes asyncpg's unique-violation exception. Other integrity failures remain
generic operation failures; driver names, constraint names, SQL, and values are not exposed.

`orbit-data` defines `RepositoryConflictError`. `orbit-sql` translates the Core error code to that
capability-level exception for repository `add` and `update`, and when a deferred constraint fails
at unit-of-work commit, so application code does not depend on SQLite or PostgreSQL error types.

## Alternatives considered

- Expose SQLite and asyncpg exceptions to repository callers: rejected because provider selection
  would change application exception handling.
- Treat all integrity failures as repository conflicts: rejected because nullability, check, and
  foreign-key violations are not equivalent to uniqueness conflicts.
- Keep one generic database error: rejected because it contradicts the repository contract and
  forces callers to parse messages or lose useful control flow.

## Consequences

Applications using repository writes and unit-of-work commits can catch
`RepositoryConflictError`. Applications using the lower-level SQL contract can inspect the stable
`database.constraint-conflict` problem code, including when PostgreSQL reports the violation during
commit.
Existing callers that expected a generic `DatabaseError` for duplicate inserts or updates should
handle the more specific exception/code. Other database failures retain their prior generic
redaction behavior. The contract tests cover Core SQLite, the repository capability, and the
PostgreSQL adapter; PostgreSQL tests use a fake asyncpg pool and do not replace live-server tests.
