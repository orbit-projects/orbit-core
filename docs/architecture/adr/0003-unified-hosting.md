# ADR 0003: Unified Uvicorn and Gunicorn hosting

Status: Accepted for pre-release Core. Date: 2026-09-16.

## Problem

Orbit previously described Uvicorn as a development server and Gunicorn as a separate production
choice. That split obscured the shared ASGI runtime contract and left no Core-level hosting model
for worker startup, shutdown or process supervision.

## Decision

Uvicorn and Gunicorn are one Orbit hosting model. Uvicorn is the ASGI worker and protocol layer;
Gunicorn is the optional pre-fork process manager for managed multi-worker execution. The separate
`uvicorn-worker` package supplies the supported Gunicorn worker class. `Runtime.asgi` is the only
application boundary passed to either host.

`orbit serve` selects the host with `--server uvicorn|gunicorn`. Uvicorn supports local reload and
single-worker execution. Gunicorn requires a `Runtime` target, starts one composed Application in
each worker through ASGI lifespan, and owns worker signals and replacement. Orbit owns lifecycle,
readiness, request draining and component/resource cleanup within each worker. The CLI replaces
itself with Gunicorn rather than retaining a supervising parent, so the process manager is the
service-visible PID and receives termination and reload signals directly.

## Consequences

The same application code and configuration are exercised in local and managed environments.
Application singletons are worker-local; shared state, locks, scheduled work and event delivery
require external adapters. Reload semantics differ: Uvicorn watches files for development,
Gunicorn replaces workers on its signals. Neither host creates distributed coordination.
