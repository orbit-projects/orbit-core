# Application composition

`Application` is the root owner of one Orbit process's Core graph. It owns configuration, the
container, service and plugin registries, routing, events, lifecycle state, health, diagnostics,
and process-local state. The object is composed once and served through its `Runtime.asgi`
boundary.

Construction requires an `ApplicationConfig` instance and rejects other objects before Core
allocates containers, registries, lifecycle state, or event resources. This keeps malformed
application composition failures explicit and local to the public boundary.

## Composition phase

Register services, plugins, routes, providers, policies, health checks, and configuration sections
before the application freezes its graph. Composition validates duplicate identities, missing
dependencies, cycles, captive scopes, route conflicts, plugin compatibility, and required
capabilities before provider acquisition or network serving begins.

Plugin setup runs during composition. It may contribute definitions to Core registries, but it must
not start background work or open durable connections before lifecycle startup. The freeze boundary
prevents a request from observing a graph that is still changing.

## Lifecycle ownership

The ASGI lifespan bridge invokes `startup()` and `shutdown()`. Startup performs configuration,
initialization, and start as one serialized transaction. If any hook fails, Core unwinds entered
children, services, plugins, and resources in reverse ownership order. Shutdown attempts every cleanup
step even when one fails and reports aggregate cleanup errors after the remaining owners have run.

Applications can register child applications. A child is validated for cycles and has exactly one
parent lifecycle owner, preventing two roots from stopping the same resources. It follows its
parent through configuration, initialization, and startup. Children stop before the parent releases
its services and providers. Child names and application instances are validated at registration;
configuration watchers likewise require callable `start` and `stop` hooks before the application
owns them. Child applications, configuration watchers, state namespaces, and admin contributions
share Core's explicit one-million registration ceiling, so composition cannot retain an unbounded
number of long-lived objects. Cycle validation uses an iterative graph walk, so deep application
trees do not depend on Python's call-stack limit. `ApplicationBuilder` applies the same service
ceiling before `build()` transfers services into the application. This hierarchy makes resource
ownership explicit.

## Context and concurrency

During a lifecycle operation, Core binds the application through
`orbit.runtime.context.current_application()`. The binding is task-local and restored after the
operation. It must not be used as a substitute for dependency injection in reusable library code.

Lifecycle operations are serialized per application. Concurrent callers share the active operation
or wait for it according to the transition being requested; reentrant calls from hooks and observer
tasks fail immediately. Health checks share in-flight work, while cancellation of one health caller
does not cancel the check used by other callers.

## Process model

Each Gunicorn worker composes and starts its own application. Core does not preload process-owned
resources in the Gunicorn master, and it does not provide cross-worker coordination. Plugins that
need shared state, locks, durable events, or scheduled work must use external provider systems.
