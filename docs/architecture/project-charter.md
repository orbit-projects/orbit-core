# Orbit project charter

This document is the normative description of Orbit's purpose, architectural boundary, and
long-term direction. It complements the implementation-specific guides: the guides describe
current behavior, while this charter records the project the Core team is building toward.

## Purpose

Orbit is a type-safe, cloud-native Python framework for service-based applications. Its job is to
orchestrate an application made of explicit services, providers, plugins, and operational
surfaces. Orbit exists for systems where service boundaries, lifecycle ownership, health, and
operator workflows should be visible in the framework rather than hidden in application glue.

Orbit is intentionally not a framework that ships every database, broker, cloud SDK, or identity
provider. Core supplies the contracts and orchestration rules; independently installable plugins
provide optional capabilities and provider-specific implementations.

## One Core Framework

Orbit Core is one foundational framework unit. It is not decomposed into Core plugins. The Core
boundary owns the architecture that every Orbit application needs:

- application composition and orchestration;
- typed configuration, state, identifiers, and runtime models;
- dependency injection, providers, scopes, and resource ownership;
- service contracts, lifecycle, health, and diagnostics;
- routing integration, events, errors, and security contracts;
- plugin registration, compatibility, and lifecycle integration;
- the runtime, CLI foundation, and administrative foundations.

The dependency direction is deliberately one-way:

```text
application -> orbit-core -> capability adapter -> provider SDK
```

Database, messaging, telemetry, cloud, storage, and identity-provider implementations belong
outside Core. A Core reference implementation may be in-memory and process-local for tests, but it
must never be described as durable or distributed merely because it satisfies a contract.

## Three first-class surfaces

Every Orbit application is built around the same Core graph and exposes three related surfaces:

```text
Orbit application
├── Web runtime
├── Admin Panel
└── CLI
```

The Web runtime serves application routes and lifecycle-owned health endpoints. The Admin Panel
inspects and, where authorized, controls the same application, service, plugin, configuration,
health, event, and diagnostic state. The CLI delegates to the same Core models and contracts for
local composition, validation, hosting, inspection, and operations. None of these surfaces should
maintain a parallel application model.

## Web and hosting direction

Orbit owns a small, provider-neutral ASGI runtime and routing boundary. Uvicorn is the development
server and direct single-worker host. Production deployments use Gunicorn as the process manager
with `uvicorn_worker.UvicornWorker` hosting the same Orbit ASGI application in each worker. Orbit
does not add another web framework to this boundary; the Core router, request/response models,
middleware, lifecycle bridge, and OpenAPI generation remain Orbit-owned contracts.

The current repository implements this hosting model. See the [ASGI guide](../runtime/asgi.md),
[deployment runbook](../deployment/README.md), and [hosting ADR](adr/0003-unified-hosting.md)
for the supported development and production commands. The hosting server owns sockets, worker
processes, and signals; Orbit owns application composition, lifecycle state, resource cleanup, and
request orchestration.

## Type safety and contracts

Type safety is an architectural property, not an optional style preference. Orbit uses Python type
hints, Pydantic models, explicit enums, strongly typed identifiers, protocols, structured errors,
and typed configuration, state, events, plugin metadata, and service contracts.

Stable contracts are preferred over concrete implementation coupling. Important contract families
include Application, Service, Provider, Container, Plugin, Lifecycle, Router, State, Identity,
Security, Authentication, and Event. A plugin may be written in Python, Rust, C++, Go, or
JavaScript/TypeScript when its boundary can be represented safely through a supported contract or
adapter protocol. The implementation language is a plugin decision; the Core contract remains the
integration boundary.

## Service-oriented orchestration

Services are the fundamental units of an Orbit application. Each service has explicit identity,
dependencies, configuration, lifecycle, state, health, and registration. Core validates the graph,
orders lifecycle work, scopes dependencies, coordinates startup and shutdown, and exposes the
result to runtime, admin, and CLI consumers.

Core should answer operational questions such as:

- Which services, plugins, providers, and routes exist?
- What dependencies and capabilities connect them?
- Which lifecycle phase and health state are active?
- Which configuration and runtime state are safe to inspect?
- Which identity and authorization context permits an operation?

The implementation must keep those answers structured and inspectable without forcing service
implementations to share a database client, broker SDK, cloud SDK, or global service locator.

## Plugin ecosystem

Plugins are optional, independently versioned capabilities. The initial ecosystem priorities are:

| Tier | Initial capability families |
| --- | --- |
| 1 — must have | PostgreSQL, Redis, HTTP client, gRPC, authentication, secrets, OpenTelemetry, Docker, Kubernetes, testing |
| 2 — Core ecosystem | Kafka, NATS, RabbitMQ, GraphQL, WebSockets, Prometheus, S3, service discovery, configuration integrations, Sentry |
| 3 — cloud integrations | AWS, GCP, Azure, Cloudflare |
| 4 — developer ecosystem | Git, GitHub, CI/CD, registries, Grafana, Loki, Jaeger, Tempo, Alertmanager |

These tiers describe ecosystem priority and maturity goals, not dependencies of Core and not a
restriction on community extensions. Plugins own provider SDKs, credentials, migrations, network
clients, deployment details, and provider-specific failure behavior. Core owns the contracts,
composition, lifecycle, health, diagnostics, and security extension points around them.

## Cloud-native and secure by default

Orbit is designed for containers, Kubernetes, horizontal scaling, stateless processes, graceful
shutdown, readiness/liveness probes, structured diagnostics, metrics, tracing, and externalized
state. Core remains cloud-provider-neutral and does not pretend that one worker's in-memory state
is a distributed system.

Core provides security and observability contracts. Authentication mechanisms, token providers,
secret managers, telemetry exporters, and cloud identity systems are plugins. Administrative
operations must remain authenticated, authorized, auditable, non-cacheable where sensitive, and
safe to expose only through an explicitly configured deployment boundary.

The project progressively aligns its repository and release process with relevant OpenSSF
practices: secure CI, dependency and secret scanning, Scorecard, vulnerability disclosure, SBOMs,
provenance, artifact signing, and release verification. These practices improve the project but do
not turn Core or an individual plugin into a deployment security certification.

## Open-source governance

Orbit is intended to be a reusable open-source project, not a reference architecture or a private
application template. The repository therefore maintains a license, contribution process, code of
conduct, security policy, maintainer responsibilities, public roadmap, ADRs, and release evidence.
Community plugins and contributors should be able to understand what belongs in Core, what belongs
in an adapter, how a change is reviewed, and which guarantees are actually tested.

The project is pre-alpha. Passing local tests, type checks, documentation checks, and security
automation is evidence for the corresponding behavior only. It is not a claim that every planned
ecosystem plugin, multi-process deployment, or CNCF/SLSA milestone is already complete. Each still
requires its own implementation and evidence. The [roadmap](../../ROADMAP.md) is the source of
truth for those gaps.

## Decision rule

When a proposed feature does not clearly belong in the universal orchestration graph, first ask
whether it can be a capability adapter or plugin. A feature belongs in Core when every Orbit
application needs its contract to compose, run, inspect, secure, or shut down safely. Provider
knowledge, vendor SDKs, and optional operational integrations belong outside Core.
