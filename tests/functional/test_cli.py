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

import sys
import types

import pytest
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


def test_serve_passes_core_asgi_to_host(target, monkeypatch):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    result = runner.invoke(app, ["serve", "orbit_test_target:runtime", "--port", "8181"])
    assert result.exit_code == 0, result.output
    assert calls[0][0][0] is target.runtime.asgi
    assert calls[0][1]["lifespan"] == "on"
    assert target.runtime.info.application_name == "cli-test"


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
