# Dependency injection and resource ownership

An application owns a root `Container`. Registrations accept a type or bounded printable string
key; empty, oversized, and control-containing keys, non-callable factories, unknown scopes, and
non-tuple dependency declarations are rejected before the provider graph is mutated. The same key
policy applies to direct `Provider` construction and resolution diagnostics.
Factories receive the container that owns their result. Declare dependencies using the
`dependencies` tuple so `validate()` can reject missing keys, cycles and captive scopes
before resource acquisition. Dynamic cycles are also rejected during resolution.

Every registered `Provider` receives a stable typed `ProviderId`. The identifier is metadata for
inspection, diagnostics, and audit correlation; dependency resolution still uses the explicit type
or string key. Replacing a registration creates a new provider definition and therefore a new
provider identity. Provider records validate factories, scopes, dependency keys, resource ownership,
and UUID identities even when an integration constructs one directly.
Each provider may declare at most 1,024 dependency edges, preventing one graph node from creating
unbounded validation work while the overall provider registry remains bounded as well.

| Scope | Cached owner | Required API |
| --- | --- | --- |
| SINGLETON | Application root | resolve or aresolve |
| SCOPED | Explicit child scope | resolve or aresolve |
| TRANSIENT | No result cache | resolve or aresolve |
| Managed resource, any scope | Root or child exit stack | aresolve |

`register_instance` registers an externally owned value; Core never guesses whether arbitrary
objects need closing. Use `register_resource` for sync or async context managers.
A successfully entered context manager is always registered for cleanup before returning
its value. If entering fails, the context manager itself must roll back partial entry.
Use `register_alias("public-name", "internal-key")` when two names must identify the same
provider instance. Aliases retain the target scope and are resolved asynchronously, so they
support async factories and managed resources. `container.dependency_graph` returns a detached
mapping suitable for diagnostics without invoking factories.

## Request-scoped resources

```python
from contextlib import asynccontextmanager
from orbit.container import Scope


@asynccontextmanager
async def transaction(container):
    database = await container.aresolve("database")
    async with database.transaction() as connection:
        yield connection


# The application must register its database adapter separately.
application.container.register_resource(
    "transaction", transaction, scope=Scope.SCOPED, dependencies=("database",)
)
```

An HTTP handler resolves through `request.container`. Its scope remains open through
stream delivery and closes after the last response frame, a disconnect or cancellation.
Non-HTTP tasks use `async with application.container.scope() as scope`.

## Concurrency and lifecycle rules

Independent child scopes have independent construction locks. Singleton acquisition uses
the root lock, so concurrent requests receive one cached instance. Nested async resolution
must be directly awaited. A factory that spawns another task to resolve dependencies is
rejected with `container.forked-resolution`; inherited task context cannot bypass ownership.

Synchronous access to a key undergoing asynchronous construction is rejected. Retry using
`aresolve`. Factories that fail or are cancelled leave no cached result. Synchronous
factories and context-manager exits must not block the event loop.

`aclose()` prevents new resolutions, waits for current construction, and exits resources
in reverse acquisition order. Concurrent close callers await the same cleanup task.
Cancellation of a close caller is deferred until cleanup completes. Each async exit has
its own `cleanup_timeout`; a cancellation-resistant exit is detached and tracked until it
finishes so later stack exits still run, and failures are aggregated after all exits are attempted.
Synchronous resource exits run on daemon worker threads under the same deadline, so blocking
third-party cleanup cannot freeze the event loop.
Application containers inherit the configured lifecycle timeout for individual exits.
Code that suppresses cancellation or blocks synchronously cannot be forcibly stopped by Core.

`Container.observe()` installs a payload-free provider resolution hook. Each uncached sync or
async construction reports its key, scope, success, elapsed time and failure type. Observer
exceptions are logged and isolated from dependency resolution; resolved values and exception
messages never enter the observation record. `ProviderResolution` validates those fields when
constructed directly as well as when emitted by the container. Observer registration is bounded by
Core's one-million callback capacity; a full observer registry fails before retaining another hook.

Close all child scopes before closing the root. `override` is a composition-time test
facility, permitted only on an unresolved, unfrozen root without child scopes. It restores
the original registration on exit. Registrations and overrides are closed after freeze.

Regression evidence: `tests/unit/container/test_concurrency.py`,
`test_resource_failures.py`, and `test_scopes.py`.
