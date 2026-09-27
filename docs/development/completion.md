# Stable Core release gate

Orbit Core is not expected to contain every provider integration. This gate defines when its
orchestration contracts are stable enough for plugin development and internal release. A plugin
must pass its own provider, security, and deployment evidence before it is called production-ready.

The Core release gate covers:
- dependency graph validation, scope isolation, resource cleanup and overrides;
- application and component state, concurrent lifecycle calls, timeout and cancellation;
- plugin metadata validation, explicit discovery, dependency ordering and cleanup;
- typed configuration composition, secret redaction, state validation and event delivery;
- ASGI request/response framing, disconnects, size/time limits, route dispatch and security;
- current health/readiness, diagnostics and an authenticated administrative surface;
- working CLI, examples, integration helpers, documentation and package builds;
- release archive integrity, including safe paths, importable contents, typing metadata, license
  inclusion and distribution metadata;
- module/public API docstrings, Markdown structure, local-link validation, and comment hygiene;
- lint, strict typing, behavioral tests, coverage and security automation.

Public compatibility and deprecation expectations are defined in the [API stability policy](api-stability.md).

The gate is complete only when these contracts are versioned, documented, exercised through the
public APIs, and safe under cancellation, partial failure, repeated lifecycle calls, and concurrent
use. “Complete” does not mean that Core ships a database, broker, identity provider, cloud client,
or telemetry backend.

Passing local checks is evidence about the implementation, not a production certification.
Provider packages are not part of this Core task. Hosted repository governance, actual
security scan results and release provenance remain deployment gates.

## Verified locally on 2026-09-28

- Python 3.11.14: 1026 behavioral tests passed, 2 opt-in hosting tests skipped, 91.41% combined line/branch coverage.
- Python 3.12.14, 3.13.15, and 3.14.7: each lock-synchronized environment passed 1026 behavioral
  tests, with 2 opt-in hosting tests skipped by default; the enabled Gunicorn/Uvicorn process
  hosting smoke test also passed for each interpreter.
- Ruff lint and formatting passed; strict mypy passed for 116 source files.
- Documentation policy and local-link validation passed across source, tests, examples, scripts,
  and repository Markdown.
- The model-boundary audit passed: Core Pydantic models use explicit strict primitive fields,
  validate declared defaults, and regression tests reject byte-to-text coercion at event, audit,
  error, secret, and security boundaries.
- Structured mapping boundaries detach and recursively freeze nested values, reject custom mutable
  mappings that would otherwise escape protection, and fail closed on cycles or excessive nesting.
- Configuration snapshots apply the same fail-closed cycle, depth, and recursive-work guarantees
  while retaining JSON-safe immutable history.
- Long-lived configuration history and in-memory state capacities are explicitly bounded to one
  million retained snapshots, entries, or namespaces at their public constructor boundaries.
- Maintained Python license-header checks, source/wheel builds and distribution-integrity checks passed.
- The frozen development/server environment installed successfully and pip-audit reported
  no known vulnerabilities; the editable Orbit package itself is excluded by that audit.
- Local release-artifact validation also generated a valid CycloneDX SBOM with 58 components and
  verified SHA-256 checksums for the source distribution and wheel; hosted provenance attestation
  remains a separate release gate.
- Regression tests cover independent scope concurrency, singleton contention, asynchronous
  acquisition/close races, cancellation-safe cleanup, per-resource failures, atomic startup,
  shared health checks, bounded plugin activation rollback, service-owned routes, typed
  configuration/provider/request identities,
  plugin routes through lifespan, operational diagnostics, admin failure isolation and
  testing-client protocol validation, concurrent overload admission, failed-handler cleanup,
  request-capacity reuse, deterministic adversarial ASGI scope handling, and stream disconnect
  cleanup ordering for request-scoped dependencies.
- The in-process ASGI test harness now has direct regression coverage for structured lifespan
  failures, silent host termination, bounded shutdown cancellation, client disconnect frames, and
  applications that emit no response.
- The maintained `examples.minimal.app:application` target is validated through the real CLI loader,
  composition checker, dependency inspection command, and one-shot health command.
- The opt-in two-worker Gunicorn smoke test passed with `uvicorn_worker.UvicornWorker` under
  Python 3.11, 3.12, 3.13, and 3.14; SIGHUP replacement workers served an HTTP route, every
  worker generation served repeated 64-request concurrent bursts, an in-flight request completed during
  graceful termination, and every generation reached service cleanup before the Gunicorn master
  terminated cleanly.
- The same opt-in process suite started direct Uvicorn development servers on all four supported
  interpreters, served repeated 16-request concurrent bursts, and completed SIGINT-driven lifespan cleanup.

## Evidence still required for deployment

- Hosted Python 3.12–3.13 CI results; all four supported interpreters now have local behavioral and
  hosting evidence, but local results do not replace hosted CI evidence.
- Public `main` has successful CodeQL and OpenSSF Scorecard runs as of 2026-09-27; rerun and
  review those results for the current Core commit, then verify repository protection and code-owner
  membership.
- Release workflow execution and resulting provenance attestations.
- Load and termination testing against the chosen host, proxy and real provider adapters.

These gates do not require adding provider implementations to Core. Distributed persistence,
identity administration, telemetry exporters and hot package installation are not silently
substituted with in-memory implementations. Core provides the orchestration and extension
contracts; plugins and deployments select and validate the corresponding adapters.
