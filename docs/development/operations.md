# Production operations and validation

See the [deployment runbook](../deployment/README.md) for the complete Gunicorn and
Uvicorn worker topology and proxy health contract.

A Gunicorn master supervises Uvicorn workers. Each worker hosts one freshly composed Application;
do not construct network connections before a worker fork. Keep startup/shutdown under ASGI
lifespan. Uvicorn direct mode and Gunicorn worker mode use the same `Runtime.asgi` boundary.
Set Gunicorn's graceful timeout above the time needed for request draining and sequential
component/resource cleanup. Individual Core timeouts are not a single process-wide deadline.
Gunicorn owns SIGTERM/SIGQUIT/SIGHUP and worker replacement; service hooks must be idempotent
under a worker restart. Uvicorn reload is for development and must not be combined with Gunicorn
workers.

Configure request body limits and concurrency limits for the deployment's memory budget.
Buffered body capacity can approach max_body_bytes × concurrent requests, plus application
and response memory. `max_concurrent_requests` defaults to 1,000 and cannot exceed the Core
one-million capacity ceiling. Set max_response_bytes to bound each buffered or streamed response; streaming
responses also retain their scoped dependencies.

Use /health/live for process responsiveness and /health/ready for traffic admission.
Readiness probes run current checks and share concurrent calls. Health checks must cooperate
with cancellation and should avoid destructive or expensive work.

## Logs and telemetry

Core uses standard-library logging without configuring the root logger. Install
`orbit.diagnostics.JSONFormatter` on a handler owned by the application or host.
It emits timestamp, logger, level, explicit message, application identity and the
server-generated request ID. Exceptions add their type; arbitrary extras, stack locals,
credentials and exception messages are not implicitly serialized. Application log messages
must themselves avoid secrets.

`application.diagnostics.subscribe(sink)` accepts a nonblocking `TelemetrySink`.
Backend failures are isolated and logged. Unsubscribe by identity when detaching an observer.
Counters include overload and distinguish completed, failed, cancelled and disconnected
requests. A started 200 response can still have a failed outcome. Recent history is bounded;
counters and fixed cumulative latency buckets retain all observed requests for the process.
Backend exporters should use queues with explicit backpressure policies.

## Release verification

Run frozen dependency installation, lint and formatting checks, strict typing, behavioral
coverage tests, license-header validation, package builds and dependency audit. CI defines
a Python 3.11–3.14 matrix and runs the managed Gunicorn/Uvicorn worker smoke test on every
matrix interpreter; local execution on one interpreter is not evidence for the others.
The release workflow also runs `scripts/check-package.py` so a wheel and source archive are
inspected for safe paths, regular archive members, matching project/version filenames, package
contents, typing metadata, license inclusion and matching project metadata before provenance is
requested.

The release workflow builds only from main, repeats validation, produces distributions and
a dependency CycloneDX inventory, validates its JSON syntax, repeats the managed
Gunicorn/Uvicorn worker smoke test, records and verifies SHA-256 checksums, and requests GitHub
build provenance for the complete artifact directory. Before signing, `scripts/check-release.py`
validates that every direct `dist/` file is covered by the checksum manifest and validates the
CycloneDX inventory. It also signs the complete `dist/` set
with Sigstore using the workflow's OIDC identity and uploads the signing artifacts. The inventory
covers the installed
build/test environment, not just runtime dependencies. Hosted execution and attestation
availability depend on repository settings.

The fixed scorecard is also a release decision gate. Run
`uv run --no-sync python scripts/check-scorecard.py --require-stable` only when the hosted CI,
deployment, and provenance evidence for the proposed release is current. A successful local
artifact-integrity check must not be presented as permission to publish while that command fails.

The repository URL is https://github.com/orbit-projects/orbit_core. The current CODEOWNERS
team entry still requires confirmation that the team exists and has write access.
Branch protection, review policy, vulnerability reporting, secret scanning and actual
OpenSSF results must be verified on GitHub. Local workflow files do not establish a badge.

Before deployment, exercise the actual ASGI host and proxy under representative concurrency,
large bodies, slow clients, dependency outages, cancelled requests and worker termination.
Use production adapter implementations in those tests. Core's in-memory state and event bus
do not provide distributed durability or exactly-once delivery.
