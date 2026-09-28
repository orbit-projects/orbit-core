# Health and readiness

Health is an operational observation owned by the application graph. Readiness is the admission
decision for new work; it is stricter than liveness and must not be inferred from process existence.

`Application.is_live` reports whether the lifecycle can still perform work. The ASGI
`/health/live` endpoint returns 200 while the application is created, starting, running or
stopping, and 503 after a terminal stop or failed startup.

`Application.health()` aggregates service checks concurrently. Readiness requires the running
phase and an overall healthy report; degraded, unknown, unhealthy and stale states do not pass
`/health/ready`. Successful reports are retained in the bounded `health_history` property and
the authenticated admin health view for operational diagnosis.
Report detail mappings are detached and recursively read-only after validation, preventing a
provider or observer from rewriting a recorded health result. Detail keys are bounded printable
strings and each report is capped at 2,048 top-level entries while it is copied, so a custom
mapping cannot bypass the limit by reporting an inaccurate length before it reaches Admin or
diagnostics.
Report timestamps must carry a usable timezone offset; malformed custom timezone implementations
fail closed as ordinary Core validation errors rather than leaking datetime implementation errors.

Plugins may optionally expose an async `health()` hook. These checks appear under namespaced
`plugin:<name>` detail keys and participate in the same aggregate status and readiness decision;
plugins without the hook remain compatible and are not probed.

Terminal supervised task failures are also included as `task:<name>` unhealthy details and
prevent `/health/ready` from returning success. This keeps background work part of the same
readiness contract as services and plugins.

Nested applications are checked under `child:<name>` keys. A degraded or unhealthy child
propagates to the parent's aggregate report and readiness state.

Plugin health checks should describe the resource they own and distinguish required outages from
optional degradation. Check names are bounded printable strings and one aggregate is capped at
1,000 checks before execution begins. Checks run concurrently under finite deadlines; a timeout or
exception is isolated to that component while independent checks finish. A check that suppresses
cancellation is detached by its own check name after its deadline or a bounded shutdown, and
repeated probes fail closed for that check until it exits, so one stuck provider cannot mark
unrelated checks as cancelling or accumulate orphan tasks. Concurrent probes for the same name also fail closed with
`Health check is already running.` if the shared provider task exceeds the waiting caller's
deadline; otherwise they receive the same completed report. A provider task cannot be replaced in
the registry by a racing probe. Health messages are bounded printable text, and provider payloads and
credentials must not be copied into health details. During startup and graceful shutdown, the
process may remain live while readiness is false. `HealthService.close()` owns a shared, shielded
cleanup task, so caller cancellation is reported only after detached checks receive their bounded
cleanup window.
