# Core completion criteria

The Core release gate covers:
- dependency graph validation, scope isolation, resource cleanup and overrides;
- application and component state, concurrent lifecycle calls, timeout and cancellation;
- plugin metadata validation, explicit discovery, dependency ordering and cleanup;
- typed configuration composition, secret redaction, state validation and event delivery;
- ASGI request/response framing, disconnects, size/time limits, route dispatch and security;
- current health/readiness, diagnostics and an authenticated administrative surface;
- working CLI, examples, integration helpers, documentation and package builds;
- lint, strict typing, behavioral tests, coverage and security automation.

Passing local checks is evidence about the implementation, not a production certification.
Provider packages are not part of this Core task. Hosted repository governance, actual
security scan results and release provenance remain deployment gates.

## Verified locally on 2026-09-14

- Python 3.11.14: 175 behavioral tests passed, 94.24% combined line/branch coverage.
- Ruff lint and formatting passed; strict mypy passed for 89 source files.
- Maintained Python license-header checks and source/wheel builds passed.
- The frozen development/server environment installed successfully and pip-audit reported
  no known vulnerabilities; the editable Orbit package itself is excluded by that audit.
- Regression tests cover independent scope concurrency, singleton contention, asynchronous
  acquisition/close races, cancellation-safe cleanup, per-resource failures, atomic startup,
  shared health checks, plugin routes through lifespan, operational diagnostics, admin
  failure isolation and testing-client protocol validation.

## Evidence still required for deployment

- Hosted Python 3.12–3.14 CI results; only Python 3.11 is installed locally.
- Actual CodeQL/Scorecard results, repository protection and verified code-owner membership.
- Release workflow execution and resulting provenance attestations.
- Load and termination testing against the chosen host, proxy and real provider adapters.

These gates do not require adding provider implementations to Core. Distributed persistence,
identity administration, telemetry exporters and hot package installation are not silently
substituted with in-memory implementations. Core provides its documented orchestration and
extension contracts; deployments select the corresponding adapters.
