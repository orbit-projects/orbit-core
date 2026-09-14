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
"""Configuration precedence and bounded, secret-safe loading."""

import pytest
from pydantic import BaseModel, ConfigDict, Field

from orbit.config.loader import load_config
from orbit.errors import ConfigurationError


class Database(BaseModel):
    host: str = "localhost"
    ports: list[int] = Field(default_factory=lambda: [5432])


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database: Database = Field(default_factory=Database)
    names: list[str] = Field(default_factory=list)


def test_json_environment_and_nested_override_preserve_other_layers(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text('[database]\nhost="file"\nports=[1111]\n')
    values = {"database": {"host": "explicit"}}
    settings = load_config(
        Settings,
        file=path,
        values=values,
        environment={
            "ORBIT_DATABASE": '{"host":"json"}',
            "ORBIT_DATABASE__HOST": "nested",
            "ORBIT_NAMES": '["api","worker"]',
        },
    )
    assert settings.database.host == "nested"
    assert settings.database.ports == [1111]
    assert settings.names == ["api", "worker"]
    assert values == {"database": {"host": "explicit"}}


@pytest.mark.parametrize(
    "environment",
    [
        {"ORBIT_DATABASE": "{secret-invalid"},
        {"ORBIT_NAMES": "[]", "ORBIT_names": '["ambiguous"]'},
        {"ORBIT_DATABASE": "scalar", "ORBIT_DATABASE__HOST": "conflict"},
        {"ORBIT__": "secret"},
    ],
)
def test_invalid_environment_errors_do_not_expose_inputs(environment):
    with pytest.raises(ConfigurationError) as error:
        load_config(Settings, environment=environment)
    assert "secret" not in str(error.value)


def test_configuration_file_limit_is_enforced(tmp_path):
    path = tmp_path / "large.toml"
    path.write_text('names=["private"]')
    with pytest.raises(ConfigurationError):
        load_config(Settings, file=path, max_file_bytes=5)
