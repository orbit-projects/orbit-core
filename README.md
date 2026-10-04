# Orbit Core

Orbit Core is the **orchestrator** for service-based Python applications. It owns composition,
dependency lifetimes, lifecycle transitions, the ASGI boundary, health, diagnostics, and operator
inspection. It deliberately does not own databases, brokers, identity providers, cloud clients, or
telemetry vendors. Those capabilities are installed as plugins and adapters against Core contracts.

The project's complete architectural direction is recorded in the [project charter](docs/architecture/project-charter.md).
Orbit Core is one foundational framework unit with three related surfaces: the web runtime, the
first-class Admin surface, and the operator CLI. Core's opt-in Admin surface is a protected HTML
overview plus typed inspection and operational endpoints; it is not a general business-data CRUD or
analytics dashboard. That richer `orbit-admin` capability is planned as a separately installed
package and is not implemented in the current workspaces. Orbit owns a small provider-neutral ASGI
and routing boundary. Development uses Uvicorn directly; production uses Gunicorn with the Uvicorn worker.
The [native ASGI decision](docs/architecture/adr/0005-native-asgi-boundary.md) records why Core
owns this boundary rather than depending on another web framework.

Optional capability packages and provider adapters are separate distributions and repositories:
they are not bundled into the `orbit-core` package, installed as its dependencies, or activated
implicitly. Install only the packages an application chooses to use. Deliberate Core baselines are
limited to universal orchestration/security contracts and small built-in behavior such as
stdlib-backed SQLite and explicitly enabled static-user Basic Auth; provider integrations and
advanced security remain optional packages.

This boundary is the central design constraint: Core coordinates a capability, while a plugin owns
the provider-specific implementation. A deployment can therefore replace a provider without
changing application lifecycle or business code.

## Development

Python 3.11–3.14 is supported by the configured CI matrix. Core tests use the separately maintained
`orbit-testing` package. Check out that repository as a sibling at `../orbit-testing` before syncing
the development environment; the locked dev dependency resolves from that local path. It is not a
runtime dependency of `orbit-core`.

Then create the environment from the committed lockfile:

```bash
python -m pip install uv==0.12.13
uv sync --frozen --extra dev --extra development-server
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
composition freezes. Production deployments should instead install the `server` extra to add
Gunicorn and `uvicorn-worker` for supervised multi-worker hosting.

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

`orbit-core` does not bundle optional packages. Current local workspaces include `orbit-data`,
`orbit-cache`, `orbit-redis`, `orbit-sql`, `orbit-sql-postgres`, `orbit-jwt`, `orbit-security`,
`orbit-testing`, `orbit-metrics`, `orbit-prometheus`, `orbit-resilience`, `orbit-logging`,
`orbit-devtools`, `orbit-gateway`, `orbit-kafka`, `orbit-migrations`, `orbit-mongo`,
`orbit-nats`, `orbit-rabbitmq`, and `orbit-vector`. They are installed separately and remain
pre-alpha; this is not a claim that the wider plugin catalog is implemented. See
[Core and plugin ownership](docs/architecture/core-and-plugin-ownership.md)
for the explicit Core exceptions and package status.

See [architecture](docs/architecture/overview.md), [dependency injection](docs/concepts/dependency-injection.md),
[lifecycle](docs/concepts/lifecycle.md), [HTTP operation](docs/runtime/asgi.md),
[admin](docs/admin/README.md), [CLI](docs/cli/README.md), and
[release gates](docs/development/completion.md).

The [public roadmap](ROADMAP.md) distinguishes implemented Core contracts from planned hardening,
ecosystem plugins, cross-language plugin transport, and long-term CNCF readiness.

## Community and governance

Contributions follow [CONTRIBUTING.md](CONTRIBUTING.md), the [Code of Conduct](CODE_OF_CONDUCT.md),
and the [governance policy](GOVERNANCE.md). The [maintainer directory](MAINTAINERS.md) records
release and Core-contract responsibility. Report vulnerabilities privately under
[SECURITY.md](SECURITY.md).

## Release status

The package remains pre-release (`0.1.0a1`). Local behavioral tests and static checks establish
specific guarantees; hosted CI, security scans and deployment-specific load testing are separate
release evidence. [Production operations](docs/development/operations.md) describes those boundaries.

Source: [orbit-projects/orbit-core](https://github.com/orbit-projects/orbit-core).
Licensed under Apache-2.0; maintained Python files carry the full license notice.
