# Production operations and validation

A process hosts one freshly composed Application per worker. Do not construct network
connections before a worker fork. Keep startup/shutdown under the ASGI lifespan protocol.
Set host shutdown grace periods above the time needed for request draining and sequential
component/resource cleanup. Individual Core timeouts are not a single process-wide deadline.

Configure request body limits and concurrency limits for the deployment's memory budget.
Buffered body capacity can approach max_body_bytes × concurrent requests, plus application
and response memory. Streaming responses also retain their scoped dependencies.

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
a Python 3.11–3.14 matrix; local execution on one interpreter is not evidence for the others.

The release workflow builds only from main, repeats validation, produces distributions and
a dependency CycloneDX inventory, and requests GitHub build provenance. The inventory covers
the installed build/test environment, not just runtime dependencies. Hosted execution and
attestation availability depend on repository settings.

The repository URL is https://github.com/orbit-projects/orbit_core. The current CODEOWNERS
team entry still requires confirmation that the team exists and has write access.
Branch protection, review policy, vulnerability reporting, secret scanning and actual
OpenSSF results must be verified on GitHub. Local workflow files do not establish a badge.

Before deployment, exercise the actual ASGI host and proxy under representative concurrency,
large bodies, slow clients, dependency outages, cancelled requests and worker termination.
Use production adapter implementations in those tests. Core's in-memory state and event bus
do not provide distributed durability or exactly-once delivery.
