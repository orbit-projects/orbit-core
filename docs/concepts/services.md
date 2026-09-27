# Services

Services are long-lived application components coordinated by Orbit Core. A service is a
provider-neutral unit of behavior with an identity, dependency metadata, lifecycle hooks, health
reporting, and inspectable capabilities. Core owns when a service is configured, initialized,
started, stopped, or restarted; the service owns the work performed by those hooks.

## Service contract

Implement `Service` for the common lifecycle defaults or implement `ServiceContract` when an
existing class already has its own base class. Every service supplies a `ServiceDescriptor` with a
stable name and identifier, semantic version, required service dependencies, advertised
capabilities, and JSON-safe metadata suitable for operators. Names, versions, and capability labels
are strict text fields; malformed or implicitly coerced values fail at construction.
Descriptor metadata is detached and recursively read-only after validation, so inspection cannot
rewrite the registered service graph. Its keys are printable, nonempty, at most 255 characters,
and limited to 2,048 entries so service inspection remains bounded.
One service may declare at most 1,024 dependency edges, keeping graph validation proportional to
the component's declared relationship set.

The descriptor is configuration data, not a service locator. Dependencies are resolved by the Core
registry and container, so a service should request providers through dependency injection instead
of importing global clients.

## Lifecycle order

Core validates the complete service graph before invoking hooks. Services configure, initialize,
and start in topological dependency order. Shutdown and failed-start rollback run in reverse order,
including the service whose hook failed. Each hook is bounded by the application's lifecycle
timeout. A service should make hooks idempotent where possible and propagate cancellation except
inside a documented cleanup section.

Registration also verifies that every lifecycle and health hook is callable and that the descriptor
passes Core validation. Core also verifies that the live descriptor still matches the registered
snapshot before lifecycle, dependency, health, or inspection work; changing a service identity
after registration fails closed instead of desynchronizing the graph. If a failing hook changes
metadata while startup is rolling back, Core still uses the frozen registration snapshot to clean
up the service. Invalid service composition is reported as a structured validation error before
any service hook runs.

Service hooks must not call application lifecycle methods recursively. They may resolve providers,
publish events, register health checks, and record diagnostics while the application context is
bound. Blocking synchronous I/O should be moved behind an adapter boundary or an executor so it
cannot stall the event loop.

## Health and operations

Services can contribute health and readiness checks. An unhealthy required dependency makes the
aggregate application unready; an optional degraded dependency is reported in diagnostics without
being silently treated as healthy. The admin API and CLI expose the same service state, dependency
graph, health report, and bounded lifecycle history.

Operational `start`, `stop`, `restart`, and `reload` controls are explicit. A failed stop or start
is recorded as `FAILED` and is never represented as a successful transition. Core does not invent
provider recovery: a plugin or deployment policy decides whether to retry, replace, or disable a
service.

## Plugin ownership

Provider-specific services belong in plugins. A database plugin may register a pool service; a
messaging plugin may register a producer and consumer service; an authentication plugin may register
token verification and key-refresh services. Core coordinates those services without depending on
their SDKs, wire protocols, credentials, or persistence models.
