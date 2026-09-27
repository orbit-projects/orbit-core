# Orbit Core handoff

## Current state

Orbit Core is a pre-alpha custom Python framework. The repository currently contains the foundational
runtime and cross-cutting contracts, but it is not certified for commercial production use. Work is
local and uncommitted; it has not been pushed to GitHub. Preserve the user's root `app.py` state if
present and do not reset or clean the worktree without explicit instruction.

## Hosting decision

The authoritative hosting model is:

```text
Reverse proxy/load balancer -> Gunicorn -> uvicorn_worker.UvicornWorker -> Orbit ASGI application
```

`orbit serve` defaults to direct Uvicorn for development. Production uses:

```bash
uv run orbit serve app:runtime --server gunicorn --workers 4
```

The project also supports direct Uvicorn, `orbit run`, and `orbit start`. The project deliberately
will not add Starlette, Litestar, or another web framework. Because Orbit owns HTTP/routing code,
future work must emphasize protocol security, fuzzing, load testing, and maintenance discipline.

## Implemented areas

- Application lifecycle phases, nested applications, task supervision with bounded cancellation
  drains, graceful cleanup, service start/stop/restart/reload, explicit failed-restart state
  transitions, application context propagation, and hosting inspection.
- Dependency injection scopes, factories, async providers, resources, aliases, introspection, and
  resolution telemetry.
- Service descriptors, dependency ordering, capabilities, health, readiness, and admin controls.
- Plugin registration/discovery, API compatibility, dependency ordering, enable/disable semantics,
  optional dependencies, capabilities, required-capability validation, and bounded activation/
  deactivation cleanup.
- Layered configuration, snapshots, diffs, reload observers, TOML watching, secret references,
  redacted secret values, secret-manager contracts, and explicit one-million-entry history caps.
- State namespaces, TTL, transactions, optimistic versions, async providers, lease coordination,
  and explicit one-million-entry/namespace capacity caps.
- Events with metadata, retries, backoff, dead letters, filters, priorities, concurrency limits,
  failure-safe deduplication before durable persistence, durable store contracts, replay, and
  application-owned event-store shutdown, with explicit one-million in-memory capacity ceilings.
- ASGI request context, validation, streaming, aggregate inbound/outbound header and body limits,
  cancellation/disconnect handling, cookies,
  compression, strict CORS, canonical path and method validation, trusted proxy handling, RFC 7239
  `Forwarded`, and optional tracing spans.
- Routing groups, middleware, route API-version metadata, OpenAPI generation, and shared error schema.
- Security policies, bearer authentication contracts, revocation, rate limiting, OAuth/OIDC/JWKS
  contracts, token validation policies, and an optional strict `PyJWTVerifier` helper (PyJWT remains
  an application deployment dependency).
- Structured Core mappings are detached and recursively immutable with explicit cycle, nesting, and
  container-work limits; direct HTTP request models enforce aggregate header, body, and query caps.
- Admin API, audit records, service/task operations, remote `AdminClient`, metrics with bounded
  label values/cardinality, Prometheus exposition, structured logging, tracing, diagnostics export, and
  reliability primitives with explicit expired-parent deadline errors.
- Remote admin transports have bounded synchronous worker and asynchronous operation budgets; timed
  out calls retain their capacity until the underlying adapter actually returns.
- CLI inspection/health/diagnostics commands, bounded `health-watch`, plus `serve`, `run`, and
  `start` hosting commands.
- CI matrix and architecture/concepts/runtime/security/operations documentation updates.
- Production deployment runbook covering the Gunicorn/Uvicorn worker topology, proxy trust,
  health probes, resource ownership, graceful shutdown, and network-level release checks.

## Validation

The latest full validation passed:

```bash
.venv/bin/uv run --no-sync ruff format src tests
.venv/bin/uv run --no-sync ruff check src tests
.venv/bin/uv run --no-sync mypy src
.venv/bin/uv run --no-sync python scripts/check-model-boundaries.py
.venv/bin/uv run --no-sync pytest -q
.venv/bin/uv run --no-sync python scripts/check-documentation.py
.venv/bin/uv run --no-sync python scripts/check-license-headers.py
.venv/bin/uv run --no-sync python -m build --no-isolation
.venv/bin/uv run --no-sync python scripts/check-package.py dist
git diff --check
```

Latest test count: **1026 passed, 2 skipped** (the opt-in hosting tests are skipped unless enabled). Full coverage validation now remains above the declared gate at **91.41%** on Python 3.11, satisfying the
declared 90% gate. This is test coverage evidence only; production load, multi-process, proxy, and
security certification work is still outstanding.

The opt-in two-worker Gunicorn smoke test has passed with `uvicorn_worker.UvicornWorker` under
Python 3.11, 3.12, 3.13, and 3.14: each worker generation served repeated 64-request concurrent bursts,
SIGHUP replacement workers served the route, an in-flight request completed during graceful
termination, every worker generation reached service cleanup, and the master terminated cleanly.
The same opt-in process suite starts a direct Uvicorn development server on each interpreter,
serves repeated 16-request concurrent bursts, and verifies SIGINT-driven lifespan cleanup.
Enable it with `ORBIT_RUN_HOSTING_TESTS=1 uv run --no-sync pytest -q tests/integration/test_gunicorn_host.py`.

Additional local compatibility validation now covers Python 3.12.14, 3.13.15, and 3.14.7: each
interpreter passes all **1026 behavioral tests** with the two opt-in hosting tests excluded from the
default run, and the enabled Gunicorn/Uvicorn process tests pass. Hosted GitHub Actions results
are still required before release claims are made.

Release checks also pass locally: the source distribution and wheel build successfully, the
package integrity verifier confirms their contents and metadata, the license-header verifier is
clean, `pip-audit --skip-editable` reports no known vulnerabilities, a 58-component CycloneDX SBOM
is valid, and SHA-256 checksums for both distributions verify. Hosted provenance attestation
remains a separate release gate.

The public repository status was checked on 2026-09-27. CodeQL and OpenSSF Scorecard completed
successfully on `main`. The newest Dependabot CI run did not reach the build steps: its Python 3.13
job failed at the license-header check because that remote commit still contains the unlicensed
scaffold `app.py`; the local worktree's maintained license check is clean and the scaffold is
removed locally. The active `main-protection` ruleset requires reviews, signatures, code scanning,
code quality, and 80% coverage, but currently has no required status-check contexts configured.
The ruleset and hosted workflows still need an authenticated maintainer to verify and update them
after the Core changes are committed and pushed.

## Highest-priority remaining work

1. Verify the hosted GitHub evidence: run the pull-request CI, CodeQL, and Scorecard workflows;
   configure the documented `protect-main` ruleset using the exact reported check names; and run
   the release workflow to confirm the SBOM, checksums, artifact upload, and provenance attestation.
2. Continue deployment-level validation of the custom HTTP/router layer with protocol fuzzing,
   load testing, proxy behavior, and real provider adapters. These are outside the deterministic
   Core unit/integration suite and cannot be certified by local tests alone.
3. Add real Gunicorn multi-worker, signal, graceful reload, reverse-proxy, TLS, HTTP/2, load, stress,
   soak, race, and failure-injection validation.
4. Integrate and lock a maintained JWT/JWKS dependency when package resolution is available; the
   current `PyJWTVerifier` helper is opt-in and provider-specific identity systems remain external.
5. Expand CLI watch/log/diagnostic streaming and remote admin operations.
6. Add adapter contract suites and production-like pressure tests for database, cache, messaging,
   storage, authentication, telemetry, and distributed-state adapters.
7. Finish release/supply-chain verification: hosted Python 3.12-3.13 CI evidence, CodeQL, Scorecard,
   vulnerability response, signed packages/releases, provenance, SBOM, PyPI, and rollback procedures.

## Working rules

- Do not claim Orbit Core is complete or commercially production-ready based only on unit tests.
- Do not replace the Gunicorn + Uvicorn-worker hosting decision.
- Do not introduce Starlette/Litestar unless the user explicitly reverses the decision.
- Keep docs and tests aligned with every implementation change.
- Do not push or commit without the user's explicit request; local commits are the safest checkpoint
  if the user is switching accounts.
