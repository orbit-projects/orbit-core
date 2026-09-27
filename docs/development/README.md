# Development

Orbit Core changes are changes to a public orchestration contract. Identify the owning boundary
before editing (application, container, plugin, adapter, ASGI, or observability), document lifecycle
and failure semantics, and keep provider SDKs in plugin packages.

Install the locked development environment and run the same checks as CI:

```bash
uv sync --frozen --extra dev --extra server
uv lock --check
uv run --no-sync ruff check src tests scripts examples
uv run --no-sync ruff format --check src tests scripts examples
uv run --no-sync mypy src/orbit
uv run --no-sync pytest --cov=orbit --cov-report=term-missing
uv run --no-sync python scripts/check-documentation.py
uv run --no-sync python scripts/check-license-headers.py
uv run --no-sync python -m build --no-isolation
uv run --no-sync python scripts/check-package.py dist
uv run --no-sync pip-audit --skip-editable --progress-spinner off
```

`check-package.py` validates the built wheel and source archive before release artifacts are
attested. It checks safe paths and regular archive members, matching project/version filenames,
importable package files, the PEP 561 typing marker, license inclusion, and the metadata that
identifies the supported Orbit Core distribution.

The authoritative package version is `src/orbit/_version.py`; Hatchling reads that declaration for
distribution metadata, and the package checker compares built artifacts against the same source.
Do not update a second version field in `pyproject.toml` because the project intentionally uses a
single version source.

Concurrency regressions use synchronization events to establish races and bounded test
deadlines to detect deadlocks. Do not rely on large sleeps to make a race likely.

Every behavior change should include a focused regression test for cancellation, timeout, partial
startup, resource cleanup, concurrency, or redaction when those guarantees are affected. Run the
real hosting test when changing worker, signal, or reload behavior.

Use `orbit.testing.TestClient` as an async context manager around a freshly composed ASGI
application. It owns real startup/shutdown messages, validates response framing, preserves
repeated request headers and translates encoded paths as an ASGI host would. Each client
is single-use. `lifespan_timeout` controls how long the harness waits for protocol progress;
exceptions from the lifespan task surface immediately.

See [operations](operations.md) for hosting and observability, [documentation conventions](documentation.md)
for source and Markdown standards, [public API stability](api-stability.md) for compatibility and
deprecation policy, and [completion criteria](completion.md) for release gates.
