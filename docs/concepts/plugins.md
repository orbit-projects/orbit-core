# Plugins and adapters

Plugins extend the orchestrator without moving provider knowledge into Core. Orbit's ecosystem may
use a layered arrangement: Core provides stable foundation contracts; a capability or adapter layer
offers a consistent user-facing API; and provider-specific plugins implement that layer for a
particular technology. A capability can also be supplied directly by one plugin when an additional
layer does not add useful consistency or reuse.

Optional packages are installed only when an application needs them; they are not dependencies
bundled into `orbit-core`. The intended optional layers are:

```text
application
├── orbit-core (orchestration and shared contracts)
├── orbit-data (repository / Unit-of-Work capability)
└── orbit-sql (SQL capability over Core's SQLDatabase contract)
    └── orbit-sql-postgres (optional PostgreSQL adapter)
        └── asyncpg (provider driver)
```

The provider adapter implements the capability's adapter contract and Core's SQL contract; the
capability package depends on its declared contracts rather than Core discovering providers.
These packages are separately installed, and an application can omit any capability it does not
use.

The current local database workspaces demonstrate the approved three-layer arrangement:

```text
Application code
└── orbit_data.Repository
    └── orbit_sql.SQLRepository
        └── Core SQLDatabase contract
            ├── Core SQLiteDatabase (built in)
            └── orbit_sql_postgres.PostgresDatabase (optional)
```

`orbit-data` defines typed repository and Unit-of-Work protocols. `orbit-sql` implements them over
Core's `SQLDatabase` contract (including Core's SQLite baseline) and owns explicit SQL adapter
selection. `orbit-sql-postgres` owns asyncpg and PostgreSQL pool lifecycle while returning a
Core-compatible SQL resource. Applications select trusted adapters explicitly; no registry scans
or imports arbitrary installed providers. These are pre-alpha local workspaces, not published
releases or stable APIs.

None of those separately installed optional packages is built into the Core distribution. The intentional
exceptions live in Core itself: provider-neutral contracts and orchestration primitives, the
stdlib-backed SQLite baseline, and opt-in static-user Basic Auth. For example, an app may use Core's
SQLite without installing PostgreSQL support; PostgreSQL requires the separately installed
`orbit-sql-postgres` adapter (and its capability packages). Likewise, Core's Basic Auth baseline
does not bundle JWT, OAuth/OIDC, an identity provider, or advanced provider integrations.
`orbit-security` currently provides optional HTTP rate-limit middleware; Core retains only the
bounded local limiter needed by its own Admin surface. Orbit has one optional `orbit-security`
package for additional security policies; its current implementation is request-level rate limiting.
Core's built-in security baseline remains available without that package, while JWT verification
and provider-specific identity integrations remain separately installable.

The capability package must define how an application selects an adapter and what happens if
multiple adapters are installed or enabled. Core's generic capability declarations do not resolve
those provider conflicts.

This layering is not a claim that Core defines a general plugin-to-plugin adapter protocol. Core's
plugin runtime provides metadata, dependency ordering, capability declarations, setup, and
lifecycle hooks. Each capability package must define and version its own adapter contract, provider
selection and conflict rules, and compatibility tests before provider plugins can rely on it. Do
not add provider implementations to Core or presume those capability-level contracts belong there.

The current Core runtime loads Python plugins from the `orbit.plugins` entry-point group and invokes
their in-process Python protocol. Metadata declares the plugin API version, semantic version,
capabilities, required dependencies, and optional dependencies. `orbit.plugins.CORE_API_VERSION` is
the single negotiated Core contract label; it is independent of the package release version, and an
incompatible contract change must increment it with migration notes. The metadata model strictly
validates plugin names, dependency names, capability identifiers, and text types before
registration; required dependencies must be installed and form an acyclic graph, while optional
dependencies participate in ordering only when present. `PluginRegistry.with_capability()`
provides deterministic capability discovery.

Rust, C++, Go, and JavaScript/TypeScript plugins are an ecosystem goal, not a capability of this
loader today. A Pydantic metadata model and Python `Protocol` do not define a cross-language ABI or
wire contract. Supporting foreign-language plugins requires an explicit, versioned boundary—such as
a carefully specified process protocol or native ABI—with lifecycle, framing, error, identity,
resource, and shutdown semantics. No such host or protocol is implemented or implied here.
Registration validates the supported Core API before any setup or activation code runs. Core keeps
a frozen metadata snapshot and verifies that plugin identity and dependency metadata do not change
after registration; mutation fails closed. Cleanup resolves plugin names from that snapshot so a
failing hook cannot corrupt rollback bookkeeping.

Enablement is explicit before freeze: `PluginRegistry.disable(name)` and `enable(name)` control
setup and activation. Disabled required dependencies fail validation; disabled optional
dependencies are omitted. Plugin operation names and capability lookups use the same bounded
identifier contracts as metadata. Composition inspection reports enabled plugin names.

Entry-point discovery is also explicit and allowlisted. Allowlist names must be bounded lowercase
identifiers and must be unique; the allowlist is capped at 1,024 names and Core validates it while
copying, before consulting package metadata, even when a custom collection reports an inaccurate
length. An empty allowlist performs no discovery. Allowlisting authorizes execution of installed
plugin code; it is not a sandbox or a substitute for package and deployment trust controls.

Registration validates plugin metadata and callable lifecycle hooks before the plugin enters the
registry. Activation and deactivation deadlines must be finite positive numbers, and malformed
metadata or operations become structured plugin errors before plugin code runs.

`PluginContract` is the minimal runtime protocol: validated metadata plus `activate()` and
`deactivate()`. The composition-time `setup(application)` hook is optional. Plugins that need no
composition contributions may implement only the minimal protocol; subclasses of the convenience
`Plugin` base class inherit a no-op `setup()` that they can override. When provided, setup runs
synchronously before composition freezes and must register resources without acquiring them.

Plugins can declare `required_capabilities`; composition validation resolves these against the
capabilities advertised by enabled plugins and fails before setup when one is unavailable. This
is Core's generic composition check, not a provider-adapter protocol: a capability package must
still define what its adapters implement and how applications select them. This keeps Core
capability-neutral while leaving package trust, signature verification, sandboxing, secrets
delivery, and provider availability to the host deployment.

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
the adapter package so applications that do not use the capability remain small and isolated.

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
composition boundary. Optional integrations are separate workspace packages; only the packages
listed in the ownership map have implementations today.
