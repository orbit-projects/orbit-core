# Architecture overview

Orbit Core is a single foundational package. The [project charter](project-charter.md) is the
normative statement of its long-term purpose and boundary. Core owns the service model, application orchestration,
typed configuration and state, lifecycle, dependency injection, event contracts, routing, the
ASGI runtime, identity contracts, and plugin runtime contracts.

The runtime model uses Uvicorn and Gunicorn together across environments. Uvicorn supplies the
ASGI worker and protocol implementation. Gunicorn supplies the pre-fork process manager, signal
handling, worker supervision, graceful replacement and multi-worker lifecycle. Local reload and
single-worker execution use Uvicorn directly; managed multi-worker execution uses Gunicorn with
the `uvicorn-worker` worker package. Orbit's `Runtime.asgi` object is the application boundary in
both modes, while neither server owns Orbit lifecycle state. Core also defines a common SQL
capability contract with a built-in SQLite implementation for local and embedded use.

External provider technology does not enter Core. Some optional features are direct plugins. For
capabilities that need a uniform API across providers, the capability package is named
`orbit-<capability>` and the provider-specific adapter is named
`orbit-<capability>-<provider>`. The installed SQL example shows how Core contracts, a reusable
capability, and a provider adapter compose without pulling the provider SDK into Core:

```text
application
├── orbit-core (orchestration and shared contracts)
└── orbit-data (repository capability)
    └── orbit-sql (SQL implementation over Core's SQLDatabase contract)
        └── orbit-sql-postgres (PostgreSQL adapter)
            └── asyncpg (provider driver)
```

An application using embedded SQLite can install `orbit-data` and `orbit-sql` without installing
`orbit-sql-postgres` or asyncpg. A direct plugin such as `orbit-jwt` can implement a Core contract
without an additional capability package when a separate abstraction would not add useful reuse.
In either arrangement, Core never installs the optional package or provider dependency implicitly.

SQLite is the deliberately small built-in local SQL baseline; server databases implement the same
Core SQL contract through separate adapters.

Core may include an in-memory implementation when it is useful for deterministic tests, but that
implementation is explicitly process-local. It must not silently become the production default for
durable state, event delivery, credentials, or telemetry.

Maintained Python files begin with Orbit's full Apache-2.0 comment header: a `2026-present Orbit
Contributors` copyright line followed by the license notice. The repository checks this convention.

`HostingConfig` is the shared validated model for host selection, bind address, port, worker
count, reload policy, and graceful timeout. It can be supplied to `Runtime` and reused by
deployment tooling so configuration validation does not depend on a particular CLI invocation.
