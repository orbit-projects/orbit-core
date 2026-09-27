# Orbit Core

Orbit Core is the **orchestrator** for service-based Python applications. It owns composition,
dependency lifetimes, lifecycle transitions, the ASGI boundary, health, diagnostics, and operator
inspection. It deliberately does not own databases, brokers, identity providers, cloud clients, or
telemetry vendors. Those capabilities are installed as plugins and adapters against Core contracts.

The project's complete architectural direction is recorded in the [project charter](docs/architecture/project-charter.md).
Orbit Core is one foundational framework unit with three related surfaces: the web runtime, the
first-class Admin Panel, and the operator CLI. Orbit owns a small provider-neutral ASGI and routing
boundary. Development uses Uvicorn directly; production uses Gunicorn with the Uvicorn worker.

This boundary is the central design constraint: Core coordinates a capability, while a plugin owns
the provider-specific implementation. A deployment can therefore replace a provider without
changing application lifecycle or business code.

## Development

Python 3.11–3.14 is supported by the configured CI matrix. Create the development environment
from the committed lockfile:

```bash
python -m pip install uv==0.12.13
uv sync --frozen --extra dev --extra server
uv lock --check
uv run --no-sync pytest --cov=orbit
uv run --no-sync ruff check src tests scripts examples
uv run --no-sync mypy src/orbit
```

## Application composition

```python
from orbit import Application, ApplicationConfig
from orbit.asgi import Response
from orbit.runtime import Runtime

application = Application(ApplicationConfig(name="orders"))


@application.router.route("/version", method="GET", name="version")
async def version(request):
    return Response.json({"service": "orders", "version": "1"})


runtime = Runtime(application)
```

Save this as `app.py`, then run `uv run --no-sync orbit serve app:runtime`.
The runtime owns startup and shutdown through ASGI lifespan. The hosting server owns sockets,
TLS and worker processes. Routes and providers contributed by plugin setup are included before
composition freezes.

## What Core guarantees

- Complete startup is serialized against shutdown. Partial startup rolls back entered
  service hooks, plugin activation and managed resources.
- Scoped providers are isolated across requests. Singleton providers share application
  ownership. Resource exits run in reverse acquisition order with individual deadlines.
- Requests have body, time and concurrency limits. Streaming retains its dependency scope.
  Overload responses and interrupted requests are included in operational diagnostics.
- Health probes share concurrent work, and component health is reflected in application state.
- Admin access is disabled by default and requires an authenticator and explicit roles.
  Configuration secrets represented by Pydantic secret types are masked in inspection.
- The CLI and admin endpoints inspect the same Core composition and state.

These are orchestration guarantees, not a claim that an in-memory reference backend is durable or
that a deployment has been load-tested. Production behavior comes from the selected plugins,
their provider contracts, and the host's deployment evidence.

## Plugin boundary

Plugins register services, routes, providers, configuration, health checks, event handlers, and
admin views during composition. Core validates plugin identity, API compatibility, dependencies,
capabilities, enablement, and cleanup before the application enters its serving phase. See the
[plugin contract](docs/concepts/plugins.md) before implementing an integration.

See [architecture](docs/architecture/overview.md), [dependency injection](docs/concepts/dependency-injection.md),
[lifecycle](docs/concepts/lifecycle.md), [HTTP operation](docs/runtime/asgi.md),
[admin](docs/admin/README.md), [CLI](docs/cli/README.md), and
[release gates](docs/development/completion.md).

The [public roadmap](ROADMAP.md) distinguishes implemented Core contracts from planned hardening,
ecosystem plugins, cross-language plugin transport, and long-term CNCF readiness.

## Release status

The package remains pre-release (`0.1.0a1`). Local behavioral tests and static checks establish
specific guarantees; hosted CI, security scans and deployment-specific load testing are separate
release evidence. [Production operations](docs/development/operations.md) describes those boundaries.

Source: [orbit-projects/orbit_core](https://github.com/orbit-projects/orbit_core).
Licensed under Apache-2.0; maintained Python files carry the full license notice.
