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
| orbit serve TARGET | Run the optional Uvicorn development host |

Inspection commands emit JSON for scripts. Errors go to stderr and failed validation/health
returns a nonzero exit code. Inspection does not call service hooks or discover plugins.
Definitions added only by plugin setup become available during actual application configuration.

Use the authenticated admin API to inspect live worker state. A local `orbit health` command
does not prove that a separately deployed process is healthy. Install the `server` extra
for the development hosting command.

Examples:
```bash
uv run --no-sync orbit check examples.minimal.app:application
uv run --no-sync orbit dependencies examples.minimal.app:application
uv run --no-sync orbit health examples.minimal.app:application
```
