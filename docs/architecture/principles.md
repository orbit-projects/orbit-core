# Architecture principles

## Core is an orchestrator

Core owns the graph and the rules that make a graph safe to run. It does not become a collection
of vendor clients. A database, message broker, identity provider, object store, or telemetry
exporter belongs in a separately versioned plugin or adapter package.

## Contracts precede implementations

Every extension point has a typed contract, lifecycle expectations, failure semantics, health
behavior, and compatibility policy. Reference implementations may be in-memory and process-local
so the contracts can be tested; they must never be described as durable production services.

## Composition is explicit and deterministic

Application composition validates identities, dependencies, capabilities, configuration, and
routes before serving traffic. Registration order is stable, dependency order is explicit, and a
frozen composition cannot be mutated accidentally at runtime.

## Ownership is visible

The component that acquires a resource owns its cleanup. Startup is transactional: if a later
component fails, already-entered components are unwound in reverse order with bounded deadlines.
Cancellation is propagated unless a documented cleanup boundary requires shielding.

## Process hosting is separate from application hosting

Orbit exposes one ASGI boundary. Uvicorn handles the ASGI protocol in each worker; Gunicorn manages
multiple workers, signals, graceful replacement, and process supervision. Neither server owns
Orbit's application resources.

## Evidence defines release status

Tests, typing, linting, and coverage demonstrate Core behavior. They do not certify a provider,
deployment, or commercial workload. Those claims require the selected plugins, host, proxy, and
operational tests.
