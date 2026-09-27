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
"""CLI inspection exposes composition without running service hooks."""

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest
from typer.testing import CliRunner

from orbit import Application, ApplicationConfig, Service, ServiceDescriptor
from orbit.cli.app import app


@pytest.mark.parametrize("command", ["services", "plugins", "routes", "config", "dependencies"])
def test_inspection_commands_are_read_only(monkeypatch, command):
    class Worker(Service):
        descriptor = ServiceDescriptor(name="worker")

        async def configure(self):
            pytest.fail("inspection must not configure services")

    application = Application(ApplicationConfig(name="cli-inspection"))
    application.register(Worker())
    module = types.ModuleType("inspection_fixture")
    module.application = application
    monkeypatch.setitem(sys.modules, "inspection_fixture", module)
    result = CliRunner().invoke(app, [command, "inspection_fixture:application"])
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    if command == "services":
        assert value[0]["name"] == "worker"
    if command == "config":
        assert value["application"]["name"] == "cli-inspection"
    if command == "dependencies":
        assert value[0]["id"]


def test_installed_command_imports_application_from_working_directory(tmp_path):
    (tmp_path / "local_application.py").write_text(
        "from orbit import Application, ApplicationConfig\n"
        'application = Application(ApplicationConfig(name="local-console"))\n'
    )
    result = subprocess.run(
        [str(Path(sys.executable).with_name("orbit")), "check", "local_application:application"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "valid" in result.stdout
