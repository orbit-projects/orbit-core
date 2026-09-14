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
from orbit.runtime import Runtime

app = typer.Typer(
    no_args_is_help=True, help="Compose, validate and run Orbit service applications."
)
console = Console(stderr=True)


def load_target(target: str) -> Application | Runtime:
    """Load a trusted explicit module:attribute target and validate its Core type."""
    if not re.fullmatch(r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*:[a-zA-Z_]\w*", target):
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
    except (ImportError, AttributeError) as exc:
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
def health(target: str) -> None:
    """Start an isolated application, check health, print JSON and cleanly shut it down."""
    application = _application(target)

    async def run() -> bool:
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


@app.command()
def serve(
    target: str, host: str = "127.0.0.1", port: int = typer.Option(8000, min=1, max=65535)
) -> None:
    """Serve a Core application using the optional Uvicorn hosting dependency."""
    try:
        import uvicorn
    except ImportError:
        console.print("Install orbit-core[server] to use the development host.")
        raise typer.Exit(1) from None
    value = load_target(target)
    runtime = value if isinstance(value, Runtime) else Runtime(value)
    uvicorn.run(runtime.asgi, host=host, port=port, lifespan="on")


__all__ = ["app", "load_target"]
