# Orbit Core

Orbit Core is the application orchestration layer for service-based Python applications.
One application owns service and plugin composition, dependency lifetimes, lifecycle state,
HTTP routing, health checks and operator inspection. Provider integrations belong in separate
adapter and implementation packages.

## Development

Python 3.11–3.14 is supported by the configured CI matrix. Create the development environment
from the committed lockfile:

```bash
python -m pip install uv==0.12.13
uv sync --frozen --extra dev --extra server
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

## Runtime guarantees

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

See [architecture](docs/architecture/overview.md), [dependency injection](docs/concepts/dependency-injection.md),
[lifecycle](docs/concepts/lifecycle.md), [HTTP operation](docs/runtime/asgi.md),
[admin](docs/admin/README.md), [CLI](docs/cli/README.md), and
[release gates](docs/development/completion.md).

## Release status

The package remains pre-release (`0.1.0a1`). Local behavioral tests and static checks establish
specific guarantees; hosted CI, security scans and deployment-specific load testing are separate
release evidence. [Production operations](docs/development/operations.md) describes those boundaries.

Source: [orbit-projects/orbit_core](https://github.com/orbit-projects/orbit_core).
Licensed under Apache-2.0; maintained Python files carry the full license notice.
