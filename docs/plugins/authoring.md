# Plugin authoring guide

This guide defines the minimum contract for an Orbit plugin that can be installed and tested
independently of Orbit Core. A plugin is an extension package, not a patch to Core. Keep provider
SDKs, credentials, migrations, network clients, and vendor-specific error handling in the plugin.

## Package shape

Use a dedicated distribution such as `orbit-database-postgres` and declare the `orbit.plugins`
entry point in its package metadata. Pin the supported Core API range, expose a typed configuration
model, and keep the import path free of network or filesystem side effects. Discovery imports code,
so the deployment environment must treat enabled plugin packages as trusted code.

## Registration and setup

Implement `PluginContract` and provide stable metadata: name, identifier, plugin API version,
semantic version, capabilities, required dependencies, optional dependencies, and required
capabilities. In `setup(application)`, register providers, services, routes, event handlers,
health checks, and admin contributions. Setup must be deterministic and must not open connections
or launch tasks; defer resource acquisition to lifecycle hooks.

## Lifecycle

Activation occurs after composition validation and before service initialization. Acquire resources
through Core's container so ownership and reverse cleanup are visible. Every task and connection must
have a finite timeout and an explicit cancellation path. Cleanup must attempt all owned resources,
remain bounded by the application deadline, and preserve the original startup failure when rollback
also reports errors.

## Configuration and secrets

Register a namespaced Pydantic model with explicit limits. Use secret references rather than placing
credentials in ordinary strings or diagnostics. Document precedence, reload behavior, validation,
rotation, and what happens to in-flight requests during a change. Never include submitted secret
values in exceptions, logs, metrics, or admin responses.

## Health, resilience, and observability

Expose liveness and readiness checks that reflect the provider's actual ability to serve requests.
Use Core deadlines, retry, circuit-breaker, and bulkhead primitives. Classify transient failures
without retrying cancellation or non-idempotent writes blindly. Register bounded metrics and spans
with stable names and low-cardinality labels; export them through a telemetry plugin.

## Required tests

Each plugin should run Core's contract tests plus provider tests covering startup rollback, repeated
shutdown, cancellation, timeouts, capacity exhaustion, credential errors, health degradation,
process restart, and upgrade compatibility. Add integration tests against the real provider and a
failure-injection suite before calling the plugin production-ready.
