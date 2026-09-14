# ADR 0002: Runtime concurrency and operational inspection

Status: Accepted for pre-release Core.

## Problem

The initial implementation serialized every dependency scope through the root lock and
treated inherited resolution context as permission to skip locking. This could serialize
unrelated requests or let child tasks duplicate construction. Scope closure could race with
resource acquisition. ASGI froze routes before plugin composition. Operational documentation
did not describe implemented behavior.

## Decision

Use owner-specific construction locks: root for singletons and child scope for scoped or
transient resources. Permit directly awaited nested resolution in the same task. Reject
factory-created child-task resolution explicitly. This avoids implicit lock reentrancy
across tasks without introducing background provider construction and ambiguous cancellation
ownership.

Closing a scope is a shared task that waits for acquisition and owns all resource exits.
Give each exit a deadline and aggregate failures. Serialize complete application startup
against stop; reject reentrant lifecycle calls by context, including inherited child tasks.

Freeze the application's router only during Core composition, after plugin setup.
Count all admitted and rejected HTTP outcomes. Share typed composition inspection between
CLI and admin. Keep telemetry and configuration backend-neutral.

## Consequences

Unrelated request scopes make progress independently. A blocked singleton can still block
other root construction, an intentional conservative ownership policy. Extension factories
must directly await nested resolution. Cleanup deadlines rely on cooperative asynchronous
code; Core cannot kill blocked threads or cancellation-suppressing extensions.

Operational interfaces use the same state and composition. They do not silently install
plugins, modify live dependency graphs or claim remote-worker inspection from a local import.

Regression tests cover concurrency with explicit synchronization events, shutdown
cancellation, resource exit failures, plugin routes through lifespan, configuration precedence,
admin extension failures and request correlation.
