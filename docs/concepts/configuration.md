# Configuration

`ApplicationConfig` is the immutable process model. It validates application identity,
environment, lifecycle/health/request deadlines, request body and concurrency limits,
and whether the admin surface is enabled.

Operational timeout, size, concurrency and rate-limit fields use strict numeric types, security/
admin flags use strict booleans, and application identity/trusted-proxy entries use strict text
types. Direct model construction therefore cannot silently change an operational policy through
string or Python `bool`-as-`int` coercion. Defaults are validated as well, so strict field
constraints apply equally to declared defaults and caller-supplied values; timeout and rate-period
attributes retain their declared `float` runtime types. Trusted proxy CIDRs are printable, bounded
to 255 characters per entry, deduplicated, validated, and bounded by Core's shared composition
capacity before forwarded identity can be used.

The Core `Config` owner has a stable typed `ConfigurationId`. Every `ConfigSnapshot` retains that
identity while its monotonic version identifies one accepted configuration state. The ID is useful
for correlating inspection, audit, and diagnostics records without confusing one configuration
owner with another.

The shared composition snapshot exposes the same `ConfigurationId` to the CLI and Admin Panel;
those surfaces do not create separate configuration identities.

Configuration snapshots and change records validate their versions and UUID ownership and reject
non-string, control-bearing, or overlong mapping keys instead of silently converting ambiguous
keys during freezing. Their
nested values remain detached and immutable after publication; cyclic, excessively deep, or
excessively large JSON-safe structures are rejected at the snapshot boundary.

Use `orbit.config.load_config(Model, file=..., values=..., environment=...)` for application or
extension models. Precedence is TOML file < explicit values < prefixed environment variables.
Nested mappings merge; inputs are not modified. Use Pydantic models with `extra="forbid"`
when unknown settings should be rejected.
Loader boundaries validate the model class, file path, prefix, mapping types, string environment
entries, and bounded recursive input work while detaching each caller-owned mapping in one pass;
malformed, cyclic, or changing operator input becomes a redacted `ConfigurationError`
rather than an incidental Python attribute/type error.

Environment names use `ORBIT_` by default, with double underscores for nested fields.
For example, `ORBIT_DATABASE__HOST` targets `database.host`. JSON arrays and objects are
decoded for structured settings. Generic extension scalars remain strings for their Pydantic
models, while primitive application scalars are decoded before strict `ApplicationConfig`
validation. A nested environment field overrides the same field in a parent JSON object.
Conflicting scalar/object paths and ambiguous casing are rejected.

Files are read with a one MiB limit by default, configurable through `max_file_bytes` up to
64 MiB. This upper bound prevents an operator-controlled configuration path from allocating an
unbounded read buffer.
File parsing and validation failures become `ConfigurationError` with affected field
locations, excluding submitted input values. The environment prefix must be a bounded printable
string of at most 255 characters, and the file-size limit must be positive.

`application.config.register(name, model)` stores a detached extension settings model. Section names
are bounded lowercase identifiers (`[a-z][a-z0-9_.-]{0,62}`), so they remain safe in inspection,
reload records, and environment/configuration tooling. `Config` itself requires an
`ApplicationConfig` owner.
Registration ends at startup. `get(name)` returns a detached copy; inspection emits
JSON-safe data. Use `SecretStr` and `SecretBytes` for credentials: plain strings are
ordinary inspectable values. Typed section registration shares Core's one-million composition
capacity ceiling. Custom serializers are trusted application code.

`config.snapshot()` captures the current redacted values with a monotonic version. The
`history` property retains snapshots created during composition, and `config.diff(snapshot)`
returns changed leaf paths with before/after values. A snapshot belongs to the `Config`
instance that created it; passing another object or a snapshot from a different configuration
is rejected instead of producing a misleading comparison. These APIs support inspection and
audit; application settings remain immutable after startup while explicitly reloadable extension
sections use the controlled reload API below.
History is bounded to 1000 snapshots by default; callers constructing the Core `Config` owner
can select a positive `history_size` up to 1,000,000 for memory-constrained deployments. Core
rejects booleans, non-integer values, and impractically large retention requests instead of
allowing Python's implicit numeric coercion or an unbounded deque allocation to alter the policy.
Snapshot mappings and nested sequences are recursively read-only; use `snapshot.as_dict()` for
a detached serialization copy. Snapshot values are limited to JSON-safe scalars (`null`, booleans,
strings, finite numbers), mappings with bounded printable string keys of at most 255 characters, and
nested sequences; arbitrary
objects and non-finite numbers are rejected even when a snapshot is constructed directly.

For extension settings that support hot updates, `config.reload_section(name, model)` provides
an atomic replacement after freeze. The model is detached and redacted before observers are
called as a veto point. Observers receive an immutable `ConfigChange` whose nested before/after
views cannot be rewritten after publication. Observers may inspect or reject the proposed change,
but recursive reloads are rejected while approval is in progress so one callback cannot create
nested version/history commits. If an observer rejects or is cancelled during the change, the old
section, version, and audit history remain intact. File watching and secret-manager polling remain
host or adapter responsibilities. Observer registration is bounded by Core's one-million callback
capacity, matching the other long-lived composition registries.

Core never implicitly reads the global process environment; pass `os.environ` explicitly.
`SecretReference`, `SecretValue`, and `SecretManager` define the backend-neutral secret boundary.
Resolved secret values carry timezone-aware resolution timestamps, printable version metadata, and
never serialize the secret material itself.
References are safe to retain in snapshots, resolved values expose an explicit `reveal()` method,
and serialization returns version metadata only. Reference authorities reject credentials, empty
ports, backslashes, and interface-scoped IPv6 zone identifiers before adapters receive them.
Secret-manager integrations and live distributed
configuration remain adapter responsibilities.
Changes to `ApplicationConfig` require composing a new application.

`ConfigWatcher` provides an explicit async polling boundary for a TOML extension section. It
records file metadata plus a bounded content digest, applies only successfully validated and observer-approved
changes, validates the initial observation without mutating the already-composed section, and
retains the previous good section when a file is temporarily invalid or missing.
Its `Config` owner, section identity, polling interval, prefix, file-size limit, watched file, and
model class are validated at construction. Start and stop operations are serialized for one
watcher, so a pre-start cancellation or concurrent restart cannot replace one task handle with
multiple polling tasks. `stop()` waits for polling cleanup and propagates caller cancellation after cleanup completes. `last_error` retains only a
sanitized exception type summary; file paths, configuration values, and observer messages are not
exposed through the watcher inspection API.

Tests: `tests/unit/config/test_loading.py` and `test_structured_environment.py`.
