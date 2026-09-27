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
both modes, while neither server owns Orbit lifecycle state.

Provider technology never enters Core directly. A capability first receives an adapter package named
`orbit-<capability>`; provider implementations then use `orbit-<capability>-<provider>`.

The intended dependency direction is:

```text
application -> orbit-core -> capability adapter -> provider SDK
```

Core may include an in-memory implementation when it is useful for deterministic tests, but that
implementation is explicitly process-local. It must not silently become the production default for
durable state, event delivery, credentials, or telemetry.

Maintained Python files begin with Orbit's full Apache-2.0 comment header: a `2026-present Orbit
Contributors` copyright line followed by the license notice. The repository checks this convention.

`HostingConfig` is the shared validated model for host selection, bind address, port, worker
count, reload policy, and graceful timeout. It can be supplied to `Runtime` and reused by
deployment tooling so configuration validation does not depend on a particular CLI invocation.
