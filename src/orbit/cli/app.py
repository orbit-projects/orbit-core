# Copyright 2026-present Orbit Contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Operator commands backed by the same Core contracts as the ASGI runtime.

Application targets are explicit module:attribute references. Loading a target executes
trusted local Python code. Inspection commands describe a newly composed process, never
pretend to inspect a remote worker. Remote privileged operations belong behind admin auth.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError
from rich.console import Console

from orbit._version import __version__
from orbit.application import Application
from orbit.config import ApplicationConfig
from orbit.diagnostics.inspection import inspect_composition
from orbit.errors import OrbitError
from orbit.runtime import HostingConfig, HostServer, Runtime

app = typer.Typer(
    no_args_is_help=True, help="Compose, validate and run Orbit service applications."
)
console = Console(stderr=True)


def load_target(target: str) -> Application | Runtime:
    """Load a trusted explicit module:attribute target and validate its Core type."""
    if not isinstance(target, str) or not re.fullmatch(
        r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*:[a-zA-Z_]\w*", target
    ):
        raise typer.BadParameter(
            "Use module:attribute, for example examples.minimal.app:application."
        )
    module, attribute = target.split(":")
    # Console entry points start with their bin directory on sys.path. Explicit local
    # targets use the caller's working directory, as running an application script does.
    # Keep it available for deferred imports made by application lifecycle hooks.
    directory = str(Path.cwd())
    if directory not in sys.path:
        sys.path.insert(0, directory)
    try:
        value: Any = getattr(importlib.import_module(module), attribute)
    except Exception as exc:
        # Target modules are trusted application code, but import failures must still be
        # presented as bounded CLI diagnostics instead of leaking arbitrary exception text.
        raise typer.BadParameter("Application target could not be imported.") from exc
    if not isinstance(value, Application | Runtime):
        raise typer.BadParameter("Target must be an Application or Runtime instance.")
    return value


def _application(target: str) -> Application:
    value = load_target(target)
    return value.application if isinstance(value, Runtime) else value


@app.command()
def version() -> None:
    """Print the installed Orbit Core version."""
    typer.echo(__version__)


@app.command()
def validate(name: str, environment: str = "development") -> None:
    """Validate minimum configuration without starting any services."""
    try:
        config = ApplicationConfig(name=name, environment=environment)
    except ValidationError:
        console.print("[red]Invalid application name or environment.[/red]")
        raise typer.Exit(1) from None
    typer.echo(f"valid: {config.name} ({config.environment})")


@app.command()
def check(target: str) -> None:
    """Validate explicitly registered component/provider graphs without acquiring resources."""
    application = _application(target)
    try:
        application.validate()
    except (OrbitError, ValueError) as exc:
        console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
        raise typer.Exit(1) from None
    typer.echo("Core composition is valid.")


@app.command("inspect")
def inspect_application(target: str) -> None:
    """Emit JSON describing a locally composed application; does not start a worker."""
    application = _application(target)
    typer.echo(application.diagnostics.collect(application).model_dump_json(indent=2))


def _inspect_section(target: str, section: str) -> None:
    snapshot = inspect_composition(_application(target))
    typer.echo(json.dumps(snapshot.model_dump(mode="json")[section], indent=2))


@app.command()
def services(target: str) -> None:
    """List locally registered services and their dependency identities as JSON."""
    _inspect_section(target, "services")


@app.command()
def plugins(target: str) -> None:
    """List registered plugin metadata without discovering or importing other plugins."""
    _inspect_section(target, "plugins")


@app.command()
def routes(target: str) -> None:
    """List registered routes, service ownership and required roles."""
    _inspect_section(target, "routes")


@app.command()
def config(target: str) -> None:
    """Print redacted application and extension configuration."""
    _inspect_section(target, "configuration")


@app.command()
def dependencies(target: str) -> None:
    """Inspect provider scopes and dependencies without constructing providers."""
    _inspect_section(target, "dependencies")


@app.command()
def tasks(target: str) -> None:
    """List registered background tasks and their current local lifecycle state."""
    application = _application(target)
    typer.echo(
        json.dumps(
            [
                {
                    "name": task.name,
                    "state": task.state,
                    "attempts": task.attempts,
                    "last_failure": (
                        {
                            "name": task.last_failure.name,
                            "attempt": task.last_failure.attempt,
                            "error_type": task.last_failure.error_type,
                            "elapsed_seconds": task.last_failure.elapsed_seconds,
                        }
                        if task.last_failure is not None
                        else None
                    ),
                }
                for task in application.tasks.infos
            ],
            indent=2,
        )
    )


@app.command()
def events(target: str) -> None:
    """List bounded local event delivery metadata without payloads."""
    application = _application(target)
    typer.echo(
        json.dumps(
            [
                {
                    "id": str(delivery.event_id),
                    "name": delivery.name,
                    "subscribers": delivery.subscriber_count,
                    "failures": delivery.failures,
                    "deduplicated": delivery.deduplicated,
                }
                for delivery in application.events.history
            ],
            indent=2,
        )
    )


@app.command()
def diagnostics(target: str) -> None:
    """Emit a bounded local diagnostics snapshot without starting the application."""
    application = _application(target)
    typer.echo(application.diagnostics.export(application, indent=2))


@app.command("diagnostics-watch")
def diagnostics_watch(
    target: str,
    interval: float = typer.Option(
        5.0, min=0.1, max=3600, help="Seconds between diagnostics snapshots."
    ),
    iterations: int = typer.Option(
        0,
        min=0,
        max=1_000_000,
        help="Snapshots to emit; 0 keeps watching until interrupted.",
    ),
) -> None:
    """Stream newline-delimited local diagnostics from one lifecycle session."""
    application = _application(target)

    async def run() -> None:
        """Keep one application lifecycle open while emitting bounded snapshots."""
        count = 0
        async with application.running():
            while iterations == 0 or count < iterations:
                typer.echo(application.diagnostics.collect(application).model_dump_json())
                count += 1
                if iterations and count >= iterations:
                    break
                await asyncio.sleep(interval)

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        return
    except Exception:
        console.print("[red]Diagnostics watch failed; inspect application logs.[/red]")
        raise typer.Exit(1) from None


@app.command()
def health(target: str) -> None:
    """Start an isolated application, check health, print JSON and cleanly shut it down."""
    application = _application(target)

    async def run() -> bool:
        """Own the temporary lifecycle used by the one-shot health command."""
        async with application.running():
            report = await application.health()
            typer.echo(report.model_dump_json(indent=2))
            return application.is_ready

    try:
        ready = asyncio.run(run())
    except Exception:
        console.print("[red]Application health session failed; inspect application logs.[/red]")
        raise typer.Exit(1) from None
    if not ready:
        raise typer.Exit(1)


@app.command("health-watch")
def health_watch(
    target: str,
    interval: float = typer.Option(5.0, min=0.1, max=3600, help="Seconds between health checks."),
    iterations: int = typer.Option(
        0,
        min=0,
        max=1_000_000,
        help="Checks to emit; 0 keeps watching until interrupted.",
    ),
) -> None:
    """Stream health snapshots from one running application lifecycle session."""
    application = _application(target)

    async def run() -> bool:
        """Keep one application lifecycle open while emitting bounded health snapshots."""
        healthy = True
        count = 0
        async with application.running():
            while iterations == 0 or count < iterations:
                report = await application.health()
                typer.echo(report.model_dump_json())
                healthy = healthy and application.is_ready
                count += 1
                if iterations and count >= iterations:
                    break
                await asyncio.sleep(interval)
        return healthy

    try:
        ready = asyncio.run(run())
    except KeyboardInterrupt:
        return
    except Exception:
        console.print("[red]Health watch failed; inspect application logs.[/red]")
        raise typer.Exit(1) from None
    if not ready:
        raise typer.Exit(1)


@app.command()
def status(target: str) -> None:
    """Report the locally composed application's lifecycle and readiness state."""
    application = _application(target)
    typer.echo(
        json.dumps(
            {
                "application": application.config.application.name,
                "phase": application.lifecycle.phase,
                "live": application.is_live,
                "ready": application.is_ready,
            },
            indent=2,
        )
    )


@app.command()
def doctor(target: str) -> None:
    """Run non-invasive composition diagnostics and emit a machine-readable report."""
    application = _application(target)
    checks: dict[str, str] = {}
    try:
        application.validate()
        checks["composition"] = "ok"
    except (OrbitError, ValueError) as exc:
        checks["composition"] = type(exc).__name__
    try:
        application.config.inspect()
        checks["configuration"] = "ok"
    except Exception as exc:
        checks["configuration"] = type(exc).__name__
    result = {
        "status": "healthy" if all(value == "ok" for value in checks.values()) else "failed",
        "checks": checks,
    }
    typer.echo(json.dumps(result, indent=2))
    if result["status"] != "healthy":
        raise typer.Exit(1)


@app.command()
def serve(
    target: str,
    host: str = "127.0.0.1",
    port: int = typer.Option(8000, min=1, max=65535),
    server: str = typer.Option("uvicorn", "--server", help="Host process: uvicorn or gunicorn."),
    workers: int = typer.Option(1, min=1, help="Worker count for managed hosting."),
    reload: bool = typer.Option(False, "--reload", help="Reload source changes (Uvicorn only)."),
    graceful_timeout: int = typer.Option(
        30, min=1, max=3600, help="Host graceful shutdown timeout in seconds."
    ),
    worker_timeout: int = typer.Option(
        30, min=1, max=3600, help="Maximum seconds a worker may be silent."
    ),
    keep_alive: int = typer.Option(5, min=0, max=3600, help="HTTP keep-alive seconds."),
    max_requests: int = typer.Option(
        0, min=0, max=10_000_000, help="Recycle workers after this many requests; 0 disables it."
    ),
    max_requests_jitter: int = typer.Option(
        0, min=0, max=1_000_000, help="Random request-recycle jitter."
    ),
) -> None:
    """Serve a Core application through the unified Uvicorn/Gunicorn hosting model."""
    if not isinstance(server, str):
        raise typer.BadParameter("Choose --server uvicorn or --server gunicorn.")
    server = server.lower()
    if server not in {"uvicorn", "gunicorn"}:
        raise typer.BadParameter("Choose --server uvicorn or --server gunicorn.")
    try:
        hosting = HostingConfig(
            server=HostServer(server),
            host=host,
            port=port,
            workers=workers,
            reload=reload,
            graceful_timeout=graceful_timeout,
            worker_timeout=worker_timeout,
            keep_alive=keep_alive,
            max_requests=max_requests,
            max_requests_jitter=max_requests_jitter,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    value = load_target(target)
    try:
        if server == "uvicorn":
            import uvicorn
    except ImportError:
        console.print("Install orbit-core[server] to use the selected host.")
        raise typer.Exit(1) from None
    if server == "uvicorn":
        if (reload or workers > 1) and not isinstance(value, Runtime):
            raise typer.BadParameter(
                "--reload and multiple workers require a Runtime target (app:runtime)."
            )
        runtime = value if isinstance(value, Runtime) else Runtime(value)
        if reload or workers > 1:
            uvicorn.run(
                target,
                host=host,
                port=port,
                lifespan="on",
                reload=reload,
                workers=workers,
                timeout_keep_alive=hosting.keep_alive,
            )
        else:
            uvicorn.run(
                runtime.asgi,
                host=host,
                port=port,
                lifespan="on",
                timeout_keep_alive=hosting.keep_alive,
            )
        return
    if reload:
        raise typer.BadParameter("--reload is supported only with --server uvicorn.")
    if not isinstance(value, Runtime):
        raise typer.BadParameter("Gunicorn requires a Runtime target (app:runtime).")
    command = [
        sys.executable,
        "-m",
        "gunicorn",
        target,
        "--bind",
        hosting.bind,
        "--workers",
        str(workers),
        "--worker-class",
        "uvicorn_worker.UvicornWorker",
        "--graceful-timeout",
        str(hosting.graceful_timeout),
        "--timeout",
        str(hosting.worker_timeout),
        "--keep-alive",
        str(hosting.keep_alive),
    ]
    if max_requests:
        command.extend(["--max-requests", str(max_requests)])
        if max_requests_jitter:
            command.extend(["--max-requests-jitter", str(max_requests_jitter)])
    try:
        raise typer.Exit(subprocess.call(command))
    except FileNotFoundError:
        console.print("Install orbit-core[server] to use Gunicorn hosting.")
        raise typer.Exit(1) from None


def _host_alias(
    target: str,
    host: str,
    port: int,
    server: str,
    workers: int,
    reload: bool,
    graceful_timeout: int,
    worker_timeout: int,
    keep_alive: int,
    max_requests: int,
    max_requests_jitter: int,
) -> None:
    """Delegate operator aliases to the single hosting implementation."""
    serve(
        target,
        host=host,
        port=port,
        server=server,
        workers=workers,
        reload=reload,
        graceful_timeout=graceful_timeout,
        worker_timeout=worker_timeout,
        keep_alive=keep_alive,
        max_requests=max_requests,
        max_requests_jitter=max_requests_jitter,
    )


@app.command("run")
def run(
    target: str,
    host: str = "127.0.0.1",
    port: int = typer.Option(8000, min=1, max=65535),
    server: str = typer.Option("uvicorn", "--server"),
    workers: int = typer.Option(1, min=1),
    reload: bool = typer.Option(False, "--reload"),
    graceful_timeout: int = typer.Option(30, min=1, max=3600),
    worker_timeout: int = typer.Option(30, min=1, max=3600),
    keep_alive: int = typer.Option(5, min=0, max=3600),
    max_requests: int = typer.Option(0, min=0, max=10_000_000),
    max_requests_jitter: int = typer.Option(0, min=0, max=1_000_000),
) -> None:
    """Run an Orbit application using the unified hosting model."""
    _host_alias(
        target,
        host,
        port,
        server,
        workers,
        reload,
        graceful_timeout,
        worker_timeout,
        keep_alive,
        max_requests,
        max_requests_jitter,
    )


@app.command("start")
def start(
    target: str,
    host: str = "127.0.0.1",
    port: int = typer.Option(8000, min=1, max=65535),
    server: str = typer.Option("uvicorn", "--server"),
    workers: int = typer.Option(1, min=1),
    reload: bool = typer.Option(False, "--reload"),
    graceful_timeout: int = typer.Option(30, min=1, max=3600),
    worker_timeout: int = typer.Option(30, min=1, max=3600),
    keep_alive: int = typer.Option(5, min=0, max=3600),
    max_requests: int = typer.Option(0, min=0, max=10_000_000),
    max_requests_jitter: int = typer.Option(0, min=0, max=1_000_000),
) -> None:
    """Start an Orbit application using the unified hosting model."""
    _host_alias(
        target,
        host,
        port,
        server,
        workers,
        reload,
        graceful_timeout,
        worker_timeout,
        keep_alive,
        max_requests,
        max_requests_jitter,
    )


@app.command("reload")
def reload_application(
    target: str,
    host: str = "127.0.0.1",
    port: int = typer.Option(8000, min=1, max=65535),
) -> None:
    """Run the development Uvicorn reloader for an explicit Runtime target."""
    _host_alias(target, host, port, "uvicorn", 1, True, 30, 30, 5, 0, 0)


__all__ = ["app", "load_target"]
