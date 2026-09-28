# Plugins and adapters

Plugins are Orbit's unit of integration. They extend the orchestrator without moving provider
knowledge into Core. An adapter is the provider-facing part of a plugin; a plugin may also expose
routes, services, configuration, health checks, event handlers, and admin inspection.

The intended dependency direction is:

```text
Orbit Core contracts <- capability adapter <- provider implementation
```

For example, a PostgreSQL plugin implements the database contract and owns its pool, migrations,
credentials, and provider-specific errors. Core only manages registration, dependency injection,
lifecycle, health, and failure reporting.

Python plugins expose the `orbit.plugins` entry-point group and use stable Core contracts. Metadata declares the plugin API version, semantic version, capabilities, required dependencies, and optional dependencies. `orbit.plugins.CORE_API_VERSION` is the single negotiated Core contract label; it is independent of the package release version, and an incompatible contract change must increment it with migration notes. The metadata model strictly validates plugin names, dependency names, capability identifiers, and text types before registration; required dependencies must be installed and form an acyclic graph, while optional dependencies participate in ordering only when present. `PluginRegistry.with_capability()` provides deterministic capability discovery. Registration validates the supported Core API before any setup or activation code runs. Core keeps a frozen metadata snapshot and verifies that plugin identity and dependency metadata do not change after registration; mutation fails closed. Cleanup resolves plugin names from that snapshot so a failing hook cannot corrupt rollback bookkeeping.

Enablement is explicit before freeze: `PluginRegistry.disable(name)` and `enable(name)` control
setup and activation. Disabled required dependencies fail validation; disabled optional
dependencies are omitted. Plugin operation names and capability lookups use the same bounded
identifier contracts as metadata. Composition inspection reports enabled plugin names.

Entry-point discovery is also explicit and allowlisted. Allowlist names must be bounded lowercase
identifiers and must be unique; the allowlist is capped at 1,024 names and Core validates it while
copying, before consulting package metadata, even when a custom collection reports an inaccurate
length. An empty allowlist performs no discovery. Allowlisting authorizes execution of installed plugin code;
it is not a sandbox or a substitute for package and deployment trust controls.

Registration validates plugin metadata and callable lifecycle hooks before the plugin enters the
registry. Activation and deactivation deadlines must be finite positive numbers, and malformed
metadata or operations become structured plugin errors before plugin code runs.

Plugins can declare `required_capabilities`; composition validation resolves these against the
capabilities advertised by enabled plugins and fails before setup when one is unavailable. This
keeps capability discovery deterministic while leaving package trust, signature verification,
sandboxing, secrets delivery, and provider availability to the host deployment.

## Plugin implementation checklist

A production plugin should document and test:

- the Core API range and provider compatibility;
- every service/provider it registers and its scope;
- startup, shutdown, retry, timeout, and cancellation behavior;
- readiness versus liveness and degraded states;
- configuration keys, secret references, and safe redaction;
- failure classification and recovery behavior;
- metrics, tracing, audit events, and bounded diagnostics;
- migration, upgrade, and rollback behavior.

Do not import a provider SDK from Core to make a plugin easier to write. Keep that dependency in
the plugin package so applications that do not use the capability remain small and isolated.

## Composition example

An application explicitly registers a plugin before the router and provider graph are frozen:

```python
from orbit import Application, ApplicationConfig

application = Application(ApplicationConfig(name="orders"))
application.plugins.register(PostgresPlugin(config_key="database"))

# Core validates dependencies and capabilities during application configuration.
# The plugin registers its provider and service through Core's container contracts.
```

The concrete plugin class is intentionally outside this repository. A plugin package owns its
configuration model and installation instructions; this example demonstrates only the stable
composition boundary.
