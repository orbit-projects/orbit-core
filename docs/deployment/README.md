# Production deployment

Orbit's supported managed deployment is a reverse proxy or load balancer in front of
Gunicorn, with `uvicorn_worker.UvicornWorker` hosting the Orbit ASGI application:

```text
client -> TLS/reverse proxy -> Gunicorn master -> Uvicorn workers -> Orbit Runtime
```

Install the server extra in the deployment environment and expose a `Runtime` target:

```bash
uv sync --frozen --extra server
uv run orbit serve app:runtime \
  --server gunicorn --host 127.0.0.1 --port 8000 \
  --workers 4 --graceful-timeout 60
```

For long-lived deployments, set the worker controls explicitly when their defaults do not fit
the workload. For example, `--worker-timeout 60 --keep-alive 10 --max-requests 10000
--max-requests-jitter 500` bounds silent workers and spreads controlled recycling across the
worker set. A jitter value requires an enabled `max_requests` value and cannot exceed it.

`Runtime` is imported by each worker. Do not preload database connections, event
transports, state providers, thread pools, or other owned resources in module import
side effects: create them in application or service startup so every worker owns and
closes its own resources. Shared state, locks, scheduled work, and event delivery need
external adapters; Core's in-memory implementations are process-local.

Gunicorn owns the listening socket, worker processes, process signals, and replacement
of crashed workers. Uvicorn owns ASGI protocol handling inside each worker. Orbit owns
lifespan startup, readiness, request draining, cancellation, and component cleanup.
`orbit serve --server gunicorn` replaces its CLI process with Gunicorn, keeping Gunicorn as the
service-visible PID so container and supervisor signals reach the process manager directly.
Orbit also owns forwarded client identity: the Orbit CLI disables host-level proxy identity
rewriting and lets Core validate the immediate peer, configured proxy CIDRs, and forwarded
values. If hosting is configured manually, keep Uvicorn's `forwarded_allow_ips` empty (and
do not enable host-level proxy rewriting) so malformed forwarded values cannot bypass Core's
fail-closed policy.
Configure Gunicorn's graceful timeout to exceed the application's lifecycle timeout and
the expected request-drain period. Lifecycle hooks must be idempotent because a worker
can be terminated and started again.

Use direct Uvicorn only for local development or source reload:

```bash
uv run orbit serve app:runtime --server uvicorn --reload
```

Do not combine source reload with Gunicorn workers. In container orchestrators, choose
either one managed Gunicorn process per container or one direct Uvicorn process per
container and let the orchestrator scale replicas; do not multiply both worker layers
without an explicit capacity model.

## Proxy and health contract

Terminate TLS and buffer slow clients at the proxy. Forward client information only when
the application config explicitly enables `trust_forwarded_headers` and lists the proxy's
CIDR in `trusted_proxies`. Otherwise the direct socket peer remains authoritative.

Route proxy health checks to `/health/live` for process responsiveness and
`/health/ready` for traffic admission. Remove a worker from traffic while readiness is
false or during graceful shutdown. Set proxy and Gunicorn body/header limits no higher
than the Orbit limits unless the extra buffering is intentional; a request body can be
held concurrently by every admitted request.

Before release, exercise the real proxy and host with representative concurrency, large
and malformed headers, slow clients, cancelled requests, worker termination, TLS, and
rolling reload. Unit tests and the in-process test client do not prove multi-process or
network behavior. The repository includes an opt-in process smoke test:

```bash
ORBIT_RUN_HOSTING_TESTS=1 uv run --no-sync pytest -q tests/integration/test_gunicorn_host.py
```

It starts two `uvicorn_worker.UvicornWorker` processes, checks an HTTP route, sends SIGHUP and
verifies repeated 64-request concurrent bursts before and after replacement workers serve the route, completes
an in-flight request during graceful SIGTERM draining, and verifies every worker generation reaches
Orbit service cleanup before the Gunicorn master exits cleanly. It also terminates one replacement
worker deliberately and verifies Gunicorn starts a replacement that serves traffic. Run it alongside proxy, TLS, load,
and failure-injection tests; this smoke test is not a substitute for sustained load or network-level
testing. The same opt-in suite also starts a direct Uvicorn process, sends repeated 16-request
concurrent bursts, sends a deliberately stalled request body and conflicting framing to verify
bounded rejection at the real socket boundary, forwards one request through a test proxy to
verify the explicit trusted-proxy identity contract, rejects an oversized header at the host
boundary, and uses SIGINT to verify the development server reaches Orbit lifespan cleanup. It also
runs eight additional concurrent 32-request rounds and a bounded thirty-second request soak to
provide stronger sustained-load evidence; this remains a smoke test, not a production-duration
soak.
