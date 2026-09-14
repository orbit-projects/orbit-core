# Lifecycle transactions

Applications are single-use owners. The normal sequence is CREATED → CONFIGURED →
INITIALIZED → STARTING → RUNNING → STOPPING → STOPPED. Failed startup ends in FAILED
after rollback. Construct a new application to restart.

`startup()` executes configuration, initialization and start as one serialized operation.
A simultaneous `stop()` waits for the entire operation. Duplicate startup is rejected
by phase validation; it never repeats successful hooks. Individual phase methods remain
available for controlled integration tests.

Plugin setup runs before graph validation and freezing. Services configure and initialize
in dependency order; plugins activate before service initialization. The container,
configuration registry, router and service registry freeze before service configuration.

Every service whose configure hook was entered is eligible for cleanup, including one
whose hook failed. Shutdown calls service stop hooks in reverse order, then deactivates
plugins and closes managed provider resources. A failure in one cleanup does not skip
remaining components. Cleanup errors are aggregated, while startup preserves its original
failure and logs rollback failures.

Lifecycle hooks and observers cannot recursively initiate lifecycle operations, including
through child tasks. Core raises `lifecycle.reentrant-operation` immediately. Hooks may
use the container, publish events and inspect current state.

`lifecycle_timeout` bounds each asynchronous service hook and each resource exit. Cancellation
waits for cleanup before propagating. Async extension code must cooperate with cancellation;
synchronous blocking and cancellation suppression are outside these deadlines.

Lifecycle state commits before observers run. Observer exceptions cannot undo committed state.
History retains the latest 100 transitions. The admin surface exposes this same history.

Concurrent health callers share an in-flight check. Cancelling one waiter does not cancel
the shared check; shutdown cancels it before closing services. Non-running applications
are unready. Healthy running service reports update the per-service snapshot as well as
application readiness.

Regression evidence: `tests/unit/application/test_transactions.py` and
`test_operation_ownership.py`.
