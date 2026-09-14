# Administrative surface

Enable `ApplicationConfig(admin_enabled=True)` and supply an `Authenticator` to the runtime.
All inspection requires `orbit.admin.read`. An absent identity produces 401; insufficient
roles produce 403. No built-in password, bearer token or permissive fallback is provided.

| Endpoint | Data or operation |
| --- | --- |
| /admin | Server-rendered overview of Core state |
| /admin/state | Application and service snapshot |
| /admin/services | Registered service identities and dependencies |
| /admin/plugins | Plugin metadata and capabilities |
| /admin/routes | Routes, ownership and required roles |
| /admin/dependencies | Provider scopes, declared dependencies and resource ownership |
| /admin/config | Redacted Pydantic configuration |
| /admin/lifecycle | Recent lifecycle transitions |
| /admin/health | Last recorded application and component health |
| /admin/diagnostics | Counters, latency buckets and bounded request history |
| /admin/events | Bounded delivery metadata without event payloads |
| /admin/extensions/{name} | Registered extension inspection model |
| POST /admin/health/refresh | Refresh health using live component checks |

Health refresh also requires `orbit.admin.write`, a nonempty Authorization header and
no browser Origin header. The authenticator must actually verify credentials. Other unsupported
methods return 405. Every runtime admin response, including errors and mutation responses,
has `Cache-Control: no-store`.

HTML values are escaped; the dashboard has frame and content-security restrictions.
Configuration uses Core's secret-aware inspection rather than independent admin settings.

Register `AdminContribution` objects before configuration. Names must be unique lowercase
slugs. Each `inspect()` returns a Pydantic model and has a health-timeout deadline.
A failing or timed-out contribution appears as unavailable without disabling Core views;
exception messages are not returned to clients. Extension implementations must use
Pydantic secret types and avoid blocking synchronous operations.

Core does not hot-install packages or rewire a running service graph. Plugin composition is
frozen at startup. Package installation and identity-provider administration belong to
their respective deployment and adapter systems.
