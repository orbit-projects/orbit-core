# Configuration

`ApplicationConfig` is the immutable process model. It validates application identity,
environment, lifecycle/health/request deadlines, request body and concurrency limits,
and whether the admin surface is enabled.

Use `load_config(Model, file=..., values=..., environment=...)` for application or extension
models. Precedence is TOML file < explicit values < prefixed environment variables.
Nested mappings merge; inputs are not modified. Use Pydantic models with `extra="forbid"`
when unknown settings should be rejected.

Environment names use `ORBIT_` by default, with double underscores for nested fields.
For example, `ORBIT_DATABASE__HOST` targets `database.host`. JSON arrays and objects are
decoded for structured settings. Scalars remain strings for Pydantic coercion, so secret
strings are not guessed into numbers or booleans. A nested environment field overrides the
same field in a parent JSON object. Conflicting scalar/object paths and ambiguous casing
are rejected.

Files are read with a one MiB limit by default, configurable through `max_file_bytes`.
File parsing and validation failures become `ConfigurationError` with affected field
locations, excluding submitted input values. A nonempty prefix and positive size limit
are required.

`application.config.register(name, model)` stores a detached extension settings model.
Registration ends at startup. `get(name)` returns a detached copy; inspection emits
JSON-safe data. Use `SecretStr` and `SecretBytes` for credentials: plain strings are
ordinary inspectable values. Custom serializers are trusted application code.

Core never implicitly reads the global process environment; pass `os.environ` explicitly.
Secret-manager integrations and live distributed configuration belong to adapters.
Configuration changes require composing a new application.

Tests: `tests/unit/config/test_loading.py` and `test_structured_environment.py`.
