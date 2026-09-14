# Development

Install the locked development environment and run the same checks as CI:

```bash
uv sync --frozen --extra dev --extra server
uv run --no-sync ruff check src tests scripts examples
uv run --no-sync ruff format --check src tests scripts examples
uv run --no-sync mypy src/orbit
uv run --no-sync pytest --cov=orbit --cov-report=term-missing
uv run --no-sync python scripts/check-license-headers.py
uv run --no-sync python -m build --no-isolation
uv run --no-sync pip-audit --skip-editable --progress-spinner off
```

Concurrency regressions use synchronization events to establish races and bounded test
deadlines to detect deadlocks. Do not rely on large sleeps to make a race likely.

Use `orbit.testing.TestClient` as an async context manager around a freshly composed ASGI
application. It owns real startup/shutdown messages, validates response framing, preserves
repeated request headers and translates encoded paths as an ASGI host would. Each client
is single-use. `lifespan_timeout` controls how long the harness waits for protocol progress;
exceptions from the lifespan task surface immediately.

See [operations](operations.md) for hosting and observability, and
[completion criteria](completion.md) for release gates.
