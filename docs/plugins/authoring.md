# Plugin authoring guide

This guide defines the minimum contract for an Orbit plugin that can be installed and tested
independently of Orbit Core. A plugin is an extension package, not a patch to Core. Keep provider
SDKs, credentials, migrations, network clients, and vendor-specific error handling outside Core.
Depending on the capability, the ecosystem may add a separately installable capability package
between Core contracts and a provider-specific adapter/plugin, so applications have a consistent
API across providers. The capability package owns the shared API; the provider adapter owns the
vendor SDK, credentials, transport, and provider-specific behavior. This is a design direction, not
a universal plugin-to-plugin contract supplied by Core. The project catalog provides planned
package names; it does not by itself define their APIs or mean they are implemented.

## Package shape

The current Core host supports Python plugins: use a dedicated distribution and declare the
`orbit.plugins` entry point in its package metadata. Pin the supported Core API range, expose a
typed configuration model, and keep the import path free of network or filesystem side effects.
Discovery imports code, so the deployment environment must treat enabled plugin packages as
trusted code. If a capability package defines a separate provider-adapter contract, document that
contract and its version range independently; Core's plugin API version does not version
capability-specific APIs.

Multi-language plugins remain a project goal, not a currently supported runtime feature. Do not
assume this in-process Python protocol can be implemented directly by Rust, C++, Go, or
JavaScript/TypeScript. A future language-neutral host boundary needs its own versioning, lifecycle,
message or ABI contract, failure behavior, and security model before such plugins can be authored.

## Registration and setup

Implement `PluginContract` and provide stable metadata: name, identifier, plugin API version,
semantic version, capabilities, required dependencies, optional dependencies, and required
capabilities. `activate()` and `deactivate()` are required lifecycle hooks. The synchronous
`setup(application)` hook is optional: implement it only when the plugin contributes providers,
services, routes, event handlers, health checks, or admin views. Setup must be deterministic and
must not open connections or launch tasks; defer resource acquisition to lifecycle hooks. The
convenience `Plugin` base class supplies a no-op setup hook for subclasses to override. A
provider-specific integration should also state which capability-level adapter contract it
implements and how compatibility is tested.

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
Core bounds its own lifecycle and request work. Application-level retry, deadline,
circuit-breaker, and bulkhead policies are optional in the separate `orbit-resilience` package.
Classify transient failures without retrying cancellation or non-idempotent writes blindly.
Register bounded metrics and spans with stable names and low-cardinality labels; export them
through a telemetry plugin.

## Required tests

Each plugin should run Core's contract tests plus provider tests covering startup rollback, repeated
shutdown, cancellation, timeouts, capacity exhaustion, credential errors, health degradation,
process restart, and upgrade compatibility. Add integration tests against the real provider and a
failure-injection suite before calling the plugin production-ready.
