# Operator CLI

CLI commands delegate to Core models and inspection. An explicit `module:attribute`
target must reference an `Application` or `Runtime`. Loading it executes trusted local
Python code. A newly imported object describes this process, not a remote worker.

| Command | Effect |
| --- | --- |
| orbit version | Print the installed version |
| orbit validate NAME --environment ENV | Validate minimum application settings |
| orbit check TARGET | Validate currently registered graphs without startup |
| orbit inspect TARGET | Emit local application state and diagnostics |
| orbit services TARGET | Emit registered service metadata |
| orbit plugins TARGET | Emit explicitly registered plugin metadata |
| orbit routes TARGET | Emit route metadata and required roles |
| orbit dependencies TARGET | Emit provider definitions without resolving factories |
| orbit config TARGET | Emit redacted application and extension settings |
| orbit health TARGET | Start an isolated instance, check health and cleanly stop |
| orbit health-watch TARGET | Stream health snapshots from one running lifecycle session |
| orbit tasks TARGET | Emit registered background task state |
| orbit events TARGET | Emit bounded event delivery metadata |
| orbit diagnostics TARGET | Emit a bounded local diagnostics snapshot |
| orbit diagnostics-watch TARGET | Stream newline-delimited local diagnostics snapshots |
| orbit status TARGET | Report local lifecycle, liveness and readiness |
| orbit doctor TARGET | Run non-invasive composition and configuration checks |
| orbit serve TARGET | Run the unified Uvicorn or Gunicorn host |
| orbit run TARGET | Alias for `serve` |
| orbit start TARGET | Alias for `serve` |
| orbit reload TARGET | Development Uvicorn source-reload mode |

Inspection commands emit detached JSON for scripts. Configuration mappings in composition
snapshots are recursively immutable inside Core and provider inspection text is bounded and
printable before serialization. Errors go to stderr and failed validation/health
returns a nonzero exit code. Inspection does not call service hooks or discover plugins.
Definitions added only by plugin setup become available during actual application configuration.
Target syntax, import failures and target types are normalized into bounded CLI diagnostics;
ordinary exception messages from imported application modules are not echoed to the operator.

`orbit serve` accepts `--server uvicorn|gunicorn`, `--workers`, `--host`, `--port`, and `--reload`.
Reload is valid only with Uvicorn. Gunicorn requires a `Runtime` target and uses the
`uvicorn-worker` ASGI worker. Orbit checks the selected host modules before starting a process and
reports the matching installation extra when they are unavailable: install
`orbit-core[development-server]` for Uvicorn-only local development or `orbit-core[server]` for
Gunicorn production hosting. Use the host's own options for proxy, TLS and process settings;
Orbit's application limits remain in `ApplicationConfig`.
For Gunicorn, the command replaces the CLI process with the Gunicorn master so container and
service-manager signals address the production process directly.

`orbit run` and `orbit start` use the same hosting validation and options as `serve`; they are
provided for operational scripts that distinguish launching from serving.

Use the authenticated admin API to inspect live worker state. A local `orbit health` command
does not prove that a separately deployed process is healthy. The `development-server` extra
installs only Uvicorn; the production `server` extra installs Uvicorn, Gunicorn, and the Uvicorn
worker integration.

Core intentionally does not provide an `orbit admin` network command yet. The remote
`AdminClient` is a transport-neutral contract; a concrete HTTP transport belongs to the
separately installable HTTP-client capability rather than being forced into the Core runtime.
Response mappings are validated and detached while copying, so a custom transport cannot bypass
Core's bounded response-body contract by reporting an inaccurate length. This keeps the Core CLI
honest: its inspection commands operate on the explicitly loaded local application, while remote
administration remains authenticated and provider-specific.

`orbit health-watch TARGET --interval 5 --iterations 10` emits one JSON health report per check
and then exits. Finite watches accept at most 1,000,000 iterations; omit `--iterations` to
continue until interrupted. A watch exits nonzero if any observed check is not ready.

`orbit diagnostics-watch TARGET --interval 5 --iterations 10` uses the same bounded iteration
policy and emits one newline-delimited diagnostics snapshot per check while one application
lifecycle session remains open. It is intended for scripts and local operator observation; it
does not connect to a separately deployed worker.

Examples:
```bash
uv run --no-sync orbit check examples.minimal.app:application
uv run --no-sync orbit dependencies examples.minimal.app:application
uv run --no-sync orbit health examples.minimal.app:application
uv run --no-sync orbit serve examples.minimal.app:runtime --server uvicorn
```
