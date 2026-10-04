# ADR 0011: Keep CORS and compression outside Core

- Status: Accepted
- Date: 2026-10-04

## Context

Core owns the native ASGI application and a middleware contract so applications can compose
request/response behavior. It also bundled CORS policy and gzip compression implementations.
Neither policy is required by the orchestration runtime, and an application that did not use
either still carried both implementations in the Core distribution.

## Decision

Move `CORSMiddleware` and `GZipMiddleware` into the separately installable `orbit-gateway`
distribution. Keep `Middleware`, `NextHandler`, and `ASGIApplication.add_middleware()` in Core.
The optional package implements the public Core request/response contracts and requires explicit
middleware registration; installation alone does not change the application pipeline.

## Consequences

- Core users who need CORS or gzip install `orbit-gateway` and import those middleware classes
  from `orbit_gateway`.
- Core remains usable without HTTP gateway policies or additional runtime dependencies.
- CORS and gzip behavior continues to be tested against Core's real ASGI request/response path,
  but those tests and implementations now belong to the optional package.
