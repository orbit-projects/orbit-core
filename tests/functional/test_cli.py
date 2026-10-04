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
"""Real Typer commands exercise application loading, graph checks and isolated health sessions."""

import os
import sys
import types

import pytest
from typer import BadParameter
from typer.testing import CliRunner

from orbit import ApplicationBuilder, ApplicationConfig, Service, ServiceDescriptor
from orbit.cli.app import app, load_target
from orbit.runtime import Runtime

runner = CliRunner()


@pytest.fixture
def target(monkeypatch):
    module = types.ModuleType("orbit_test_target")
    module.application = (
        ApplicationBuilder(ApplicationConfig(name="cli-test")).service(ServiceImpl()).build()
    )
    module.runtime = Runtime(module.application)
    monkeypatch.setitem(sys.modules, "orbit_test_target", module)
    return module


class ServiceImpl(Service):
    descriptor = ServiceDescriptor(name="cli-service")


@pytest.mark.parametrize(
    "args,code,output",
    [
        (["version"], 0, "0.1.0a1"),
        (["validate", "valid-name"], 0, "valid"),
        (["validate", "INVALID"], 1, "Invalid"),
        (["check", "orbit_test_target:application"], 0, "valid"),
        (["status", "orbit_test_target:application"], 0, '"phase"'),
        (["doctor", "orbit_test_target:application"], 0, '"healthy"'),
        (["tasks", "orbit_test_target:application"], 0, "[]"),
        (["events", "orbit_test_target:application"], 0, "[]"),
        (["diagnostics", "orbit_test_target:application"], 0, '"request_count"'),
        (["inspect", "orbit_test_target:runtime"], 0, "cli-service"),
        (["health", "orbit_test_target:application"], 0, "healthy"),
        (["check", "does_not_exist:app"], 2, "could not be imported"),
        (["check", "invalid target"], 2, "module:attribute"),
    ],
)
def test_commands(target, args, code, output):
    result = runner.invoke(app, args)
    assert result.exit_code == code, result.output
    assert output in result.output


def test_check_failure_has_nonzero_exit(target):
    service = ServiceImpl()
    from orbit.types import new_service_id

    service.descriptor = ServiceDescriptor(name="broken", dependencies=(new_service_id(),))
    target.application.register(service)
    result = runner.invoke(app, ["check", "orbit_test_target:application"])
    assert result.exit_code == 1


def test_bad_target_type_is_rejected(target):
    target.bad = object()
    with pytest.raises(Exception, match="Application or Runtime"):
        load_target("orbit_test_target:bad")


def test_target_import_failures_are_sanitized(tmp_path, monkeypatch):
    (tmp_path / "broken_target.py").write_text(
        "raise RuntimeError('secret import details')\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(BadParameter) as error:
        load_target("broken_target:application")
    assert "could not be imported" in str(error.value)
    assert "secret import details" not in str(error.value)


def test_target_type_validation_is_explicit():
    with pytest.raises(BadParameter, match="module:attribute"):
        load_target(None)  # type: ignore[arg-type]


def test_serve_passes_core_asgi_to_host(target, monkeypatch):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    result = runner.invoke(app, ["serve", "orbit_test_target:runtime", "--port", "8181"])
    assert result.exit_code == 0, result.output
    assert calls[0][0][0] is target.runtime.asgi
    assert calls[0][1]["lifespan"] == "on"
    assert calls[0][1]["timeout_keep_alive"] == 5
    assert calls[0][1]["proxy_headers"] is False
    assert calls[0][1]["forwarded_allow_ips"] == ""
    assert target.runtime.info.application_name == "cli-test"
    assert target.runtime.info.service_count == 1
    assert target.runtime.info.child_count == 0
    assert target.runtime.info.hosting.server == "uvicorn"


@pytest.mark.parametrize("command", ["run", "start"])
def test_host_aliases_use_the_same_uvicorn_path(target, monkeypatch, command):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    result = runner.invoke(app, [command, "orbit_test_target:runtime", "--port", "8182"])
    assert result.exit_code == 0, result.output
    assert calls[0][0][0] is target.runtime.asgi
    assert calls[0][1]["port"] == 8182


def test_reload_command_uses_uvicorn_source_reload(target, monkeypatch):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    result = runner.invoke(app, ["reload", "orbit_test_target:runtime", "--port", "8183"])
    assert result.exit_code == 0, result.output
    assert calls[0][0][0] == "orbit_test_target:runtime"
    assert calls[0][1]["reload"] is True
    assert calls[0][1]["workers"] == 1
    assert calls[0][1]["proxy_headers"] is False
    assert calls[0][1]["forwarded_allow_ips"] == ""


def test_serve_gunicorn_uses_uvicorn_worker(target, monkeypatch):
    calls = []
    monkeypatch.setattr(
        os, "execv", lambda executable, command: calls.append((executable, command))
    )
    result = runner.invoke(
        app,
        [
            "serve",
            "orbit_test_target:runtime",
            "--server",
            "gunicorn",
            "--workers",
            "3",
            "--graceful-timeout",
            "45",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[0][0] == sys.executable
    command = calls[0][1]
    assert command[0:3] == [sys.executable, "-m", "gunicorn"]
    assert "uvicorn_worker.UvicornWorker" in command
    assert command[command.index("--forwarded-allow-ips") + 1] == ""
    assert "45" in command


@pytest.mark.parametrize(
    ("server", "missing_module", "extra"),
    [
        ("uvicorn", "uvicorn", "development-server"),
        ("gunicorn", "uvicorn_worker", "server"),
    ],
)
def test_serve_reports_missing_host_extra(target, monkeypatch, server, missing_module, extra):
    import importlib

    cli_module = importlib.import_module("orbit.cli.app")
    real_import_module = cli_module.importlib.import_module

    def import_module(name):
        if name == missing_module:
            raise ImportError(name)
        return real_import_module(name)

    monkeypatch.setattr(
        cli_module.importlib,
        "import_module",
        import_module,
    )
    result = runner.invoke(
        app,
        ["serve", "orbit_test_target:runtime", "--server", server],
    )
    assert result.exit_code == 1
    assert f"Install orbit-core[{extra}]" in result.output


def test_serve_gunicorn_wires_worker_lifecycle_controls(target, monkeypatch):
    calls = []
    monkeypatch.setattr(
        os, "execv", lambda executable, command: calls.append((executable, command))
    )
    result = runner.invoke(
        app,
        [
            "serve",
            "orbit_test_target:runtime",
            "--server",
            "gunicorn",
            "--worker-timeout",
            "61",
            "--keep-alive",
            "11",
            "--max-requests",
            "1000",
            "--max-requests-jitter",
            "100",
        ],
    )
    assert result.exit_code == 0, result.output
    command = calls[0][1]
    assert command[command.index("--timeout") + 1] == "61"
    assert command[command.index("--keep-alive") + 1] == "11"
    assert command[command.index("--max-requests") + 1] == "1000"
    assert command[command.index("--max-requests-jitter") + 1] == "100"


def test_serve_rejects_gunicorn_application_target(target):
    result = runner.invoke(app, ["serve", "orbit_test_target:application", "--server", "gunicorn"])
    assert result.exit_code == 2
    assert "requires a Runtime" in result.output


def test_health_failure_and_unhealthy_exit(target):
    from orbit.health import HealthReport, HealthStatus

    class Unhealthy(Service):
        descriptor = ServiceDescriptor(name="unhealthy")

        async def health(self):
            return HealthReport(status=HealthStatus.UNHEALTHY)

    target.application.register(Unhealthy())
    assert runner.invoke(app, ["health", "orbit_test_target:application"]).exit_code == 1


def test_health_startup_failure_is_reported(target):
    class Bad(Service):
        descriptor = ServiceDescriptor(name="bad")

        async def start(self):
            raise ValueError("start failure")

    target.application.register(Bad())
    result = runner.invoke(app, ["health", "orbit_test_target:application"])
    assert result.exit_code == 1 and "failed" in result.output


def test_health_watch_emits_bounded_json_snapshots(target):
    result = runner.invoke(
        app,
        ["health-watch", "orbit_test_target:application", "--iterations", "1", "--interval", "0.1"],
    )
    assert result.exit_code == 0, result.output
    assert '"status":' in result.output


def test_diagnostics_watch_emits_bounded_json_snapshots(target):
    result = runner.invoke(
        app,
        [
            "diagnostics-watch",
            "orbit_test_target:application",
            "--iterations",
            "1",
            "--interval",
            "0.1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert '"request_count":' in result.output


def test_health_watch_returns_failure_for_unready_application(target):
    from orbit.health import HealthReport, HealthStatus

    class Unhealthy(Service):
        descriptor = ServiceDescriptor(name="watch-unhealthy")

        async def health(self):
            return HealthReport(status=HealthStatus.UNHEALTHY)

    target.application.register(Unhealthy())
    result = runner.invoke(
        app,
        ["health-watch", "orbit_test_target:application", "--iterations", "1"],
    )
    assert result.exit_code == 1


def test_health_watch_rejects_unbounded_finite_iteration_requests(target):
    result = runner.invoke(
        app,
        ["health-watch", "orbit_test_target:application", "--iterations", "1000001"],
    )
    assert result.exit_code == 2
    assert "1000000" in result.output
