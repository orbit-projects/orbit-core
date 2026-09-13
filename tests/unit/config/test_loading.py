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
"""Layering, validation and secret handling across extension configuration."""

import pytest
from pydantic import BaseModel, ConfigDict, SecretStr

from orbit.config import ApplicationConfig
from orbit.config.config import Config
from orbit.config.loader import load_application_config, load_config
from orbit.errors import ConfigurationError


class Database(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str
    password: SecretStr


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database: Database


def test_nested_configuration_precedence_and_redaction(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text('[database]\nhost="file"\npassword="private"\n')
    settings = load_config(
        Settings,
        file=file,
        values={"database": {"host": "mapping"}},
        environment={"ORBIT_DATABASE__HOST": "environment"},
    )
    assert settings.database.host == "environment"
    config = Config(ApplicationConfig(name="test"))
    config.register("database", settings)
    assert "private" not in str(config.inspect())
    config.freeze()
    with pytest.raises(ValueError):
        config.register("other", settings)


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"ORBIT_NAME": "INVALID"},
        {"ORBIT_NAME": "test", "ORBIT_UNKNOWN": "secret"},
        {"ORBIT_NAME": "test", "ORBIT_REQUEST_TIMEOUT": "-1"},
        {"ORBIT_NAME": "test", "ORBIT__": "x"},
    ],
)
def test_invalid_config_never_exposes_submitted_values(environment):
    with pytest.raises(ConfigurationError) as caught:
        load_application_config(environment)
    assert "secret" not in str(caught.value)
    assert "secret" not in caught.value.problem.model_dump_json()


def test_environment_coercion():
    config = load_application_config({"ORBIT_NAME": "test", "ORBIT_ADMIN_ENABLED": "true"})
    assert config.admin_enabled
