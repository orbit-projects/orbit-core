# Orbit Core stabilization tracking

This checklist is the progress measure for Core stabilization, effective 2026-10-01. It
replaces informal percentage estimates for ongoing work. Items are binary: count an item only
when its stated evidence passes on the current candidate. Do not infer completion from a plan,
an older run, or a passing scorecard checker.

## Local Core hardening

These are the implementation and verification areas Orbit Core can address in this worktree.

| Checkpoint | Current state | Evidence required |
| --- | --- | --- |
| Public contracts and static checks | Verified | Public API regression tests and strict mypy pass. |
| Runtime reliability and failure handling | Verified | Lifecycle, cancellation, concurrency, cleanup, state, event, container, and task-supervision regression tests pass. |
| Security boundaries | Verified | Security, authentication, authorization, proxy, request-validation, and redaction regression tests pass. |
| ASGI protocol and hosting behavior | Verified | ASGI behavior tests and opt-in Uvicorn/Gunicorn process tests pass; production topology remains Gunicorn with `uvicorn-worker`. |
| Supported Python compatibility | Verified | Full test suite and strict checks pass on Python 3.11, 3.12, 3.13, and 3.14. |
| Optional package boundaries | Verified | Packaging-boundary tests reject optional distributions as Core dependencies or bundled wheel packages, build the current Core wheel and inspect its actual archive roots, and require status documentation for the requested catalog. The latest isolated Python 3.11 environment installed Core and the original 23 current sibling workspaces together; all 23 suites passed (294 tests) with Ruff, format, and strict mypy clean. Separate `orbit-gcs` and `orbit-azure-storage` workspaces then passed 7 and 8 fake-client/SDK tests respectively, plus Ruff, format, strict mypy, wheel/sdist builds, and installed-wheel tests. Core's latest source suite passed 1,037 tests with 2 opt-in hosting tests skipped; Core Ruff, formatting, strict mypy, documentation/link, license-header, and scorecard checks passed. Earlier complete package-matrix runs cover prior workspaces on Python 3.11–3.14; these newest package validations were on Python 3.11. Existing composition coverage includes Core→repository→PostgreSQL adapter, SQL rollback/conflict mapping, SQLite cancellation/rollback serialization, bounded pool shutdown, JWT algorithm-family validation, Prometheus output bounds, resilience pre-admission validation, Core→orbit-security request throttling, SQL migrations against SQLite, Mongo repository/plugin behavior with fake driver objects, broker adapters with fake clients, and bounded S3/GCS/Azure streaming with fake clients. Provider-specific live services were not exercised. These local results do not replace hosted CI or deployment validation. |
| Documentation, packaging, and local supply-chain checks | Verified | Documentation/link and license checks, source/wheel build, package integrity, lock validation, and dependency audit pass. |

After adding shared, cancellation-resistant SQLite close completion for concurrent callers, the
exact current Core worktree passed its full test suite on Python 3.11, 3.12, 3.13, and 3.14:
**1,036 passed and 2 opt-in hosting tests skipped on each interpreter**. The suite includes a
repeated-cancellation regression proving SQLite retains its transaction lock until already-submitted
worker work finishes. Tox installs Core in
editable mode; import-origin checks confirmed all four tox environments load `src/orbit`, avoiding
stale installed Core copies on later runs. The opt-in Uvicorn/Gunicorn worker-process tests were
run explicitly on all four tox interpreters and passed **2 tests each**. Ruff, strict mypy, and the
lint environment's documentation, model-boundary, workflow, scorecard, and license-header checks
also passed. These are local host-process checks; they do not replace hosted CI or actual
reverse-proxy/load deployment validation.

The packaging-boundary guard also enumerates the complete requested Orbit distribution catalog and
statically rejects direct imports from known provider SDK roots. This is a regression guard for
ordinary imports, not proof against dynamic loading or future SDK names. The test-only ASGI harness
is now in the separate `orbit-testing` workspace; its contract tests and Core/plugin test imports
use the `orbit_testing` package, while Core exports the public ASGI types and bounds it needs.

Local checkpoints describe the latest recorded evidence, not a permanent certification. Re-run
the relevant checks after code changes; if any fail, mark the affected checkpoint open until
the regression is fixed and verified. Add a checkpoint only for a concrete uncovered risk or
requirement—do not create work merely to increase this count.

## External release gates

These are required before claiming a stable release, but are not local coding progress and are
not under this worktree's control. Keep each open until the exact candidate has evidence.

| Gate | Current state | Evidence required |
| --- | --- | --- |
| Public API baseline approval | Open | Maintainer compatibility review, recorded exceptions/deprecations, and an approved release version/tag. |
| Hosted repository security | Open | Current hosted CI, CodeQL, OpenSSF Scorecard and secret-scanning results, plus verified branch rules and code-owner access. |
| Deployment validation | Open | Tests against the selected production host and actual proxy under representative load, slow clients, upstream failures, cancellation, and worker termination. |
| Release provenance | Open | Protected hosted release run with verified artifacts, SBOM, checksums, signing, provenance, publication, and rollback evidence. |

## Reporting rule

Report local hardening checkpoints and external release gates separately as `passed / total`,
with the names of any open items. Do not combine them into a single percentage or call a passing
local checklist a stable release. The fixed weighted score in
[`completion.md`](completion.md) remains a separate release-readiness gate; it is not the
progress metric for day-to-day hardening work.

The latest recorded local validation is summarized in
[`completion.md`](completion.md#azure-blob-storage-adapter-orbit-azure-storage-2026-10-05). Current branch and
GitHub handoff details are in [`HANDOFF.md`](../../HANDOFF.md).
