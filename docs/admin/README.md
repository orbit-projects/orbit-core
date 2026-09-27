# Administrative surface

Enable `ApplicationConfig(admin_enabled=True)` and supply an `Authenticator` to the runtime.
All inspection requires `orbit.admin.read`. An absent identity produces 401; insufficient
roles produce 403. No built-in password, bearer token or permissive fallback is provided.

| Endpoint | Data or operation |
| --- | --- |
| /admin | Server-rendered overview of Core state |
| /admin/state | Application and service snapshot |
| /admin/services | Registered service identities and dependencies |
| /admin/tasks | Supervised background task state and bounded failures |
| /admin/plugins | Plugin metadata and capabilities |
| /admin/routes | Routes, ownership and required roles |
| /admin/dependencies | Provider scopes, declared dependencies and resource ownership |
| /admin/config | Redacted Pydantic configuration |
| /admin/lifecycle | Recent lifecycle transitions |
| /admin/health | Last recorded application and component health |
| /admin/diagnostics | Counters, latency buckets and bounded request history |
| /admin/events | Bounded delivery metadata without event payloads |
| /admin/audit | Bounded administrative mutation audit records |
| /admin/extensions/{name} | Registered extension inspection model |
| POST /admin/health/refresh | Refresh health using live component checks |
| POST /admin/services/{name}/restart | Restart an isolated leaf service |
| POST /admin/services/{name}/reload | Reload a service or use its restart fallback |
| POST /admin/services/{name}/start | Start an initialized stopped service |
| POST /admin/services/{name}/stop | Stop an isolated leaf service |

Health refresh also requires `orbit.admin.write`, a nonempty Authorization header and
no browser Origin header. The authenticator must actually verify credentials. Other unsupported
methods return 405. Every runtime admin response, including errors and mutation responses,
has `Cache-Control: no-store`.

Service start, stop, restart and reload also require `orbit.admin.write` and explicit non-browser
authorization. Service and task targets must be bounded lowercase slugs, matching `AdminClient`;
malformed names fail with 400 before lookup or lifecycle work. Core rejects stopping or restarting a service while a running service depends
on it, preventing a dependent graph from observing a stopped dependency. Starting a service
requires all declared dependencies to be running.

Successful and failed administrative mutations are retained in a bounded `/admin/audit` view.
Records contain action, target, principal subject/provider, outcome and a safe error code; raw
credentials, request bodies and exception messages are never recorded. Audit text rejects control
characters, error codes are bounded identifier-shaped values, and timestamps must be timezone-aware.

HTML values are escaped; the dashboard has frame and content-security restrictions.
Configuration uses Core's secret-aware inspection rather than independent admin settings.

Register `AdminContribution` objects before configuration. Registration validates the protocol,
the bounded lowercase name, and the callable inspection method. Names must be unique lowercase
slugs. Each `inspect()` returns a Pydantic model from an awaitable and has a health-timeout deadline.
A failing or timed-out contribution appears as unavailable without disabling Core views;
exception messages are not returned to clients. A cancellation-resistant contribution is detached
and only one late inspection is retained per extension, so repeated admin requests cannot create
an unbounded set of orphan tasks. Extension implementations must use Pydantic secret types and
avoid blocking synchronous operations.

Core does not hot-install packages or rewire a running service graph. Plugin composition is
frozen at startup. Package installation and identity-provider administration belong to
their respective deployment and adapter systems.

`AdminClient` is the typed remote-client boundary. It sends bearer credentials, allowlists
inspection sections, validates service and task operations, and converts structured remote
failures into `AdminClientError`; only final `4xx–5xx` failures are representable, and malformed
remote error codes are replaced with a safe fallback.
Supply an `AdminTransport` adapter to control HTTP pooling,
TLS, proxies, retries, and certificate policy without adding a network dependency to Core.
Credentials and operation identifiers are bounded and validated before an adapter call. Async
transport calls use a bounded operation budget as well; a timed-out call retains its slot until
the underlying adapter task actually returns, so distinct stalled paths cannot accumulate unlimited
late work. Each transport invocation receives a detached header mapping, so an adapter cannot mutate
the client's stored bearer credential or affect concurrent requests. Core does not silently coerce
remote status or client timeout configuration values. The synchronous worker-slot and asynchronous
operation budgets are each bounded to 1,000,000. `AdminHTTPResponse` also validates a final `2xx–5xx`
status and its top-level JSON object, then returns a detached, recursively immutable body, so
transport-owned response data cannot change after the adapter returns. Response keys must already be strings; Core does not silently
coerce malformed remote JSON keys into operator-facing data. Synchronous transports run outside the event loop with
bounded worker threads. Async callable objects are recognized as async transports. A timed-out
or cancelled call retains its worker slot or one per-operation detached async task until the
underlying transport returns, so a stalled adapter cannot create unbounded background work;
waiting for an available synchronous slot is also bounded by the request timeout.
