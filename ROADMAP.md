# Orbit roadmap

This roadmap turns the [project charter](docs/architecture/project-charter.md) into an honest,
reviewable sequence of work. Dates are intentionally omitted while the project is pre-alpha;
scope and evidence matter more than calendar promises.

The current development phase is Core-only. Orbit's plugin registry and extension contracts remain
part of Core because orchestration needs stable extension points, but no database, messaging,
authentication-provider, cloud, telemetry, or other plugin implementation is being built in this
phase.

## Current foundation

Orbit Core currently provides the provider-neutral orchestration foundation for:

- typed application configuration, state, identifiers, services, providers, and events;
- dependency injection with scopes, overrides, graph validation, and resource cleanup;
- explicit lifecycle transitions, nested applications, health/readiness, diagnostics, and tasks;
- ASGI request/response handling, routing metadata, limits, security primitives, and hosting;
- plugin contracts, metadata, dependency ordering, capabilities, and lifecycle integration;
- authenticated Admin Panel foundations and a Typer/Rich operator CLI;
- documentation, tests, type checks, coverage, repository security automation, and release checks.

These are Core contracts and reference implementations. They do not include provider SDKs or claim
durability, distributed coordination, load-test evidence, or production certification.

## Next Core work

1. Harden the Orbit-owned ASGI and router boundary while preserving the single Core graph,
   lifecycle ownership, routing metadata, dependency contracts, and Uvicorn/Gunicorn hosting model.
2. Stabilize the public Core API through compatibility policy, deprecations, migration notes, and
   clear extension-contract versioning for future adapter authors.
3. Harden the custom and integrated HTTP boundaries with protocol, fuzzing, slow-client, proxy,
   WebSocket, load, race, and failure-injection tests.
4. Expand remote operational workflows for the Admin Panel and CLI without coupling business logic
   to command handlers or exposing unauthenticated worker state.
5. Publish executable examples and contract-test templates for service, plugin, authentication,
   health, observability, and configuration integrations.

## Ecosystem work

Build the initial plugin ecosystem in the documented tiers, beginning with database/cache,
HTTP/gRPC, authentication/secrets, OpenTelemetry, container, Kubernetes, and testing adapters.
Each plugin must be independently installable, documented, tested against Core contracts, and clear
about its provider, credential, durability, and operational assumptions.

After the first-party foundations, support community adapters for messaging, storage, cloud
providers, service discovery, observability systems, and developer workflows. Plugin quality must
not be measured only by whether it imports: lifecycle rollback, readiness, security, redaction,
upgrade, migration, and failure behavior are part of the contract.

## Long-term direction

- Establish a stable cross-language plugin protocol for components where Python is not the right
  implementation language, with explicit versioning, serialization, security, and process
  ownership rules.
- Improve OpenSSF Best Practices maturity toward Silver and Gold where the project can provide real
  evidence, including signed releases, SBOMs, provenance, vulnerability response, and reproducible
  release checks.
- Grow governance and maintainership across organizations and evaluate CNCF Sandbox readiness only
  after the project has demonstrated reusable software, community adoption, transparent governance,
  and sustained release operations.

## Out of scope for Core

Orbit Core will not absorb vendor SDKs, a mandatory database or broker, a fixed identity provider,
cloud-specific deployment logic, a telemetry backend, or hot package installation into a running
application. Those capabilities may be excellent plugins; keeping them outside Core is what lets a
minimal Orbit application remain small and lets operators choose the right provider.
