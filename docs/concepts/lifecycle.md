# Lifecycle transactions

Applications are single-use owners. The normal sequence is CREATED → CONFIGURED →
INITIALIZED → STARTING → RUNNING → STOPPING → STOPPED. Failed startup ends in FAILED
after rollback. Construct a new application to restart.

Nested applications are lifecycle children of their parent. Parent phase operations invoke
children in registration order; shutdown reverses that ownership order and always drains
children before releasing parent services and providers.

`startup()` executes configuration, initialization and start as one serialized operation.
A simultaneous `stop()` waits for the entire operation. Duplicate startup is rejected
by phase validation; it never repeats successful hooks. Individual phase methods remain
available for controlled integration tests.

Operational service restarts are explicit at the state boundary: if a stop hook fails, the
service is marked FAILED and application health becomes unhealthy instead of leaving an ambiguous
STOPPING state. A failed start follows the same failure state and requires operator recovery or
a fresh application process.

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

Plugin activation and deactivation hooks are bounded independently. A hook that suppresses
cancellation is detached at its deadline and retained per plugin phase; cleanup does not invoke a
plugin's deactivation hook while its activation hook is still cancelling. This prevents startup or
shutdown from hanging and avoids concurrent provider lifecycle calls, while requiring the plugin to
eventually honor cancellation before its detached work can retire.

`lifecycle_timeout` bounds each asynchronous service hook, plugin hook and each resource exit. Cancellation
waits for cleanup before propagating. Async extension code must cooperate with cancellation;
synchronous blocking and cancellation suppression remain provider defects even though Core keeps
the owning lifecycle operation bounded.

Lifecycle transition history uses timezone-aware timestamps so chronology remains comparable across
workers and deployments. Lifecycle state commits before observers run. Observer exceptions cannot undo committed state.
Async observers have an explicit bounded deadline; a cancellation-resistant observer is detached
once and skipped while it unwinds, then its late result is consumed before it can receive another
transition. Application shutdown cancels observer work that outlived a transition. Observer
registration requires a callable; transition targets are validated as Core lifecycle phases and
invalid targets produce a structured lifecycle error. History retains the latest 100 transitions.
The admin surface exposes this same history.

Supervised task failure observers follow the same ownership rule: if an observer exceeds its
notification deadline, shutdown gives it the remaining task-supervisor budget to finish after
cancellation. A provider that still suppresses cancellation remains detached and is consumed
later without extending shutdown indefinitely.

Concurrent health callers share an in-flight check. Cancelling one waiter does not cancel
the shared check; shutdown cancels it and gives detached cancellation-resistant checks the
application lifecycle timeout to finish before closing services. A provider that still suppresses
cancellation remains tracked until its late result retires. Non-running applications are unready.
Healthy running service reports update the per-service snapshot as well as application readiness.

Regression evidence: `tests/unit/application/test_transactions.py` and
`test_operation_ownership.py`.
