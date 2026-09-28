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

## Orbit Core release scorecard

Orbit uses this fixed 100-point scorecard for progress reports. It is a repository measurement
system, not a claim that a universal industry percentage exists. Each point requires current,
reviewable evidence; an unverified hosted or deployment result is not counted as passing.

| Area | Weight | What earns the points |
| --- | ---: | --- |
| Core contracts and scope | 25 | Application/service/lifecycle/DI, configuration/state/events, ASGI/routing/hosting, health/admin/security, extension contracts and public API are implemented and documented. |
| Reliability and operational behavior | 20 | Cancellation, concurrency, cleanup, protocol limits, failure injection, multi-process hosting, proxy, load and soak behavior are tested at the appropriate layer. |
| Security and supply chain | 15 | Security boundaries, redaction, scanning, dependency auditing, signed artifacts and provenance have evidence. |
| Tests and compatibility | 20 | Full behavioral suite, the declared coverage gate, supported Python versions, hosting smoke tests, lint and strict typing pass. |
| Documentation and API governance | 10 | Public docstrings, comments, guides, examples, API stability policy and ADRs are maintained. |
| Packaging and release operations | 10 | Build integrity, licenses, SBOM, checksums, hosted release workflow, rollback and release verification pass. |
| **Total** | **100** | **Stable release requires both the score threshold and every mandatory release gate.** |

The score is reported as `earned / 100`, followed by the category breakdown. A score of 90 or
more is necessary but not sufficient for a stable release. The mandatory gates are: the local Core
validation gate, hosted CI/security evidence for the release commit, deployment-level HTTP and
hosting evidence, and verified release artifacts/provenance. Plugins and community adoption are
tracked separately and do not affect this Core score.

The machine-readable source for the fixed weights and current evidence state is
[`completion-scorecard.toml`](completion-scorecard.toml). Validate and print it with
`python scripts/check-scorecard.py`; use `--require-stable` only when evaluating a release
candidate. The checker rejects category, weight, score, or gate-name drift so progress reports
cannot silently change their denominator or claim an open mandatory gate as complete.

Current evidence score: **87 / 100** — Core contracts 23/25, reliability 19/20, security and
supply chain 10/15, tests and compatibility 20/20, documentation and API governance 10/10, and
packaging and release operations 5/10. The local Core gate passes; hosted workflow results,
deployment pressure testing, and hosted release provenance remain open and therefore are not
counted as complete.

Passing local checks is evidence about the implementation, not a production certification.
Provider packages are not part of this Core task. Hosted repository governance, actual
security scan results and release provenance remain deployment gates.

## Verified locally on 2026-09-28

- Python 3.11.14: 1108 behavioral tests passed, 2 opt-in hosting tests skipped, 91.96% combined line/branch coverage.
- Python 3.12.14, 3.13.15, and 3.14.7: each lock-synchronized environment passed 1108 behavioral
  tests, with 2 opt-in hosting tests skipped by default; the enabled Gunicorn/Uvicorn process
  hosting smoke test also passed for each interpreter.
- The hosted quality matrix is configured to report every supported interpreter independently,
  retain one coverage XML artifact per interpreter, and bound CodeQL and Scorecard runner time.
- Ruff lint and formatting passed; strict mypy passed for 116 source files.
- Public API regression coverage verified that every documented Core package exposes unique,
  resolvable exports, with the root `__version__` export explicitly preserved.
- Documentation policy and local-link validation passed across source, tests, examples, scripts,
  and repository Markdown.
- The model-boundary audit passed: Core Pydantic models use explicit strict primitive fields,
  validate declared defaults, and regression tests reject byte-to-text coercion at event, audit,
  error, secret, and security boundaries.
- Structured mapping boundaries detach and recursively freeze nested values, reject custom mutable
  mappings that would otherwise escape protection, and fail closed on cycles or excessive nesting.
- Configuration snapshots apply the same fail-closed cycle, depth, and recursive-work guarantees
  while retaining JSON-safe immutable history.
- Configuration loading detaches explicit, file, and environment mappings during their bounded
  validation pass, so mutable custom inputs cannot change between validation and composition.
- Long-lived configuration history and in-memory state capacities are explicitly bounded to one
  million retained snapshots, entries, or namespaces at their public constructor boundaries.
- Maintained Python license-header checks, source/wheel builds and distribution-integrity checks passed.
- The frozen development/server environment installed successfully and pip-audit reported
  no known vulnerabilities; the editable Orbit package itself is excluded by that audit.
- Local release-artifact validation also generated a valid CycloneDX SBOM with 58 components and
  verified SHA-256 checksums for the source distribution and wheel; hosted provenance attestation
  remains a separate release gate. The release checker also rejects unlisted direct artifacts and
  symlinked release metadata before signing.
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
  composition checker, dependency inspection command, and one-shot health command; its explicit
  `examples.minimal.app:runtime` target is also validated for Uvicorn/Gunicorn hosting.
- The opt-in two-worker Gunicorn smoke test passed with `uvicorn_worker.UvicornWorker` under
  Python 3.11, 3.12, 3.13, and 3.14; SIGHUP replacement workers served an HTTP route, every
  worker generation served repeated 64-request concurrent bursts, one replacement worker was
  terminated deliberately and replaced, an in-flight request completed during graceful termination,
  and every generation reached service cleanup before the Gunicorn master terminated cleanly.
- The same opt-in process suite started the supported `orbit serve --server uvicorn` development
  path on all four supported interpreters, served repeated 16-request concurrent bursts plus eight
  32-request sustained-load rounds and a bounded thirty-second request soak, rejected a stalled
  body and conflicting framing at
  the real socket boundary, rejected an oversized header with either a bounded HTTP response or
  host-level connection close, verified valid and malformed
  trusted-proxy identity through a forwarding hop in both direct Uvicorn and Gunicorn workers, and
  completed SIGINT-driven lifespan cleanup.

## Evidence still required for deployment

- Hosted CI, CodeQL, and OpenSSF Scorecard runs are successful for public `main` commit
  `aaf49f27ad34eed130ebdcf13e5bec624fecfb5d` as of 2026-09-28, but those runs predate the current
  unpushed Core tree; rerun and review them after push, then verify repository protection and
  code-owner membership.
- The active `main-protection` ruleset still has an empty required-status-check list, so the exact
  matrix contexts must be added after a successful pull request.
- The public release-workflow history has no runs; execute it and verify artifact upload, SBOM,
  checksums, Sigstore signing artifacts, and build-provenance attestations.
- Load and termination testing against the chosen host, proxy and real provider adapters.

These gates do not require adding provider implementations to Core. Distributed persistence,
identity administration, telemetry exporters and hot package installation are not silently
substituted with in-memory implementations. Core provides the orchestration and extension
contracts; plugins and deployments select and validate the corresponding adapters.
