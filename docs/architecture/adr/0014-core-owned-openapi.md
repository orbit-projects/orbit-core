# ADR 0014: Keep OpenAPI generation in Core

- Status: Accepted
- Date: 2026-10-04

## Context

Orbit Core owns its native ASGI router, typed route metadata, request validation, and HTTP runtime.
The router currently generates an OpenAPI 3.1 document, and the ASGI application serves that
document at `/openapi.json`. The requested package catalog also includes `orbit-docs`, so the
ownership of these existing behaviors needs to be explicit.

## Decision

Keep OpenAPI generation from Core route contracts and the default `/openapi.json` endpoint in
Core. These are part of the framework's routing and application contract, not a third-party
documentation backend. A future separately installed `orbit-docs` package may add optional
rendered documentation, hosting, or external documentation integrations, but it must not replace
or be required for Core's OpenAPI output.

## Alternatives considered

- Move generation and serving to `orbit-docs`: rejected because Core's own route contract should
  remain inspectable without an optional package, and this would make a foundational routing
  surface contingent on a sibling distribution.
- Keep only the schema model in Core and require an optional plugin to serve it: rejected because
  the current built-in endpoint is part of the default native ASGI runtime and has no external
  provider dependency.

## Consequences

- Core continues to expose `Router.openapi()` and serves `/openapi.json` by default.
- Core retains responsibility for validating route metadata and producing a deterministic schema.
- `orbit-docs` remains an optional catalog item; no documentation UI, generator backend, or hosting
  integration is implied to exist until implemented in its own repository.
- This decision does not add a web framework or another runtime dependency.

## Evidence

- `src/orbit/routing/router.py`
- `src/orbit/asgi/application.py`
- `tests/integration/test_operational_runtime.py`
- `docs/concepts/routing.md`
- `docs/architecture/core-and-plugin-ownership.md`
