# HTTP and ASGI operation

Expose `Runtime(application).asgi` or `ASGIApplication(application)` to a lifespan-enabled
ASGI server. Application composition owns router freezing, after plugin setup; lifespan
does not freeze plugin-contributed routes prematurely.

The [ASGI HTTP specification](https://asgi.readthedocs.io/en/latest/specs/www.html) defines
the host/framework boundary. The host supplies decoded paths and body chunks, and owns
HTTP parsing, transfer encoding, TLS and worker processes. Core never decodes the path twice.

## Request contract

HTTP bodies are buffered up to `max_body_bytes`. Both declared Content-Length and cumulative
received bytes are checked. Repeated or malformed Content-Length is rejected; extreme numeric
values cannot become an internal integer-conversion error. Repeated headers remain distinct.
Query parsing preserves repeated values and caps field count.

Handlers receive `Request`, with headers, JSON/schema validation, path parameters, query
data and a scoped dependency container. The server generates the request ID; client-supplied
IDs do not become trusted correlation identity. `current_request_id()` exposes it to
nested code, and the response carries `x-request-id`.

`request_timeout` bounds buffered reading, authentication, dispatch and response delivery.
Error response delivery is also bounded. Once response headers have started, a stream failure
propagates to the host instead of attempting a second HTTP response.

## Routing and middleware

Routes are associated with optional service identities and required roles. HEAD uses GET
when no explicit HEAD route exists and sends no body. OPTIONS and 405 expose allowed methods.
Middleware executes in registration order, with the first middleware outermost.

The health paths and admin namespace are reserved. Live reports process responsiveness;
ready reflects current application and service health. Authentication is supplied through
the provider-neutral authenticator contract. Application and principal context are restored
on every request exit.

## Ownership and shutdown

A request scope remains alive for the entire streamed response. A send-side OSError indicates
a disconnect; stream and dependency cleanup still run. Disconnects encountered while sending
an error response are handled the same way.

At shutdown, the runtime rejects new work, waits for active requests for the lifecycle
timeout, then cancels unfinished requests and waits for their cleanup before stopping Core.
Shutdown itself is shared and shielded against caller cancellation. Handlers cannot await
runtime shutdown because that would wait on themselves.

Overload responses return 503 with correlation headers and contribute to diagnostics.
The request timeout and shutdown deadlines require cooperative async extension code.

WebSocket upgrades are explicitly rejected by this Core HTTP release. The testing client
exercises real ASGI messages; socket, proxy and worker behavior requires deployment tests.
