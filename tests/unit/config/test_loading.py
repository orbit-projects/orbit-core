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

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from orbit.config import ApplicationConfig, ConfigWatcher
from orbit.config import watcher as watcher_module
from orbit.config.config import Config, ConfigChange, ConfigSnapshot
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


def test_application_defaults_preserve_declared_numeric_types() -> None:
    """Validated defaults must retain the strict runtime types exposed by the model."""
    config = ApplicationConfig(name="test")
    assert isinstance(config.lifecycle_timeout, float)
    assert isinstance(config.health_timeout, float)
    assert isinstance(config.request_timeout, float)
    assert isinstance(config.admin_rate_period, float)


def test_application_header_limit_is_bounded():
    with pytest.raises(ValidationError):
        ApplicationConfig(name="test", max_header_bytes=512)
    with pytest.raises(ValidationError):
        ApplicationConfig(name="test", max_header_bytes=17 * 1024 * 1024)
    with pytest.raises(ValidationError, match="trusted_proxies"):
        ApplicationConfig(name="test", trusted_proxies=("not-an-network",))
    with pytest.raises(ValidationError, match="trusted_proxies"):
        ApplicationConfig(name="test", trusted_proxies=(None,))  # type: ignore[arg-type]


def test_application_concurrency_limit_is_bounded() -> None:
    """Request admission cannot be configured beyond Core's shared capacity ceiling."""
    with pytest.raises(ValidationError):
        ApplicationConfig(name="test", max_concurrent_requests=1_000_001)


@pytest.mark.parametrize(
    "field",
    [
        "lifecycle_timeout",
        "health_timeout",
        "request_timeout",
        "max_body_bytes",
        "max_response_bytes",
        "max_header_bytes",
        "max_concurrent_requests",
        "admin_rate_limit",
        "admin_rate_period",
    ],
)
def test_application_numeric_limits_reject_booleans(field: str) -> None:
    with pytest.raises(ValidationError, match="must not be booleans"):
        ApplicationConfig(name="test", **{field: True})


@pytest.mark.parametrize(
    "field",
    [
        "lifecycle_timeout",
        "health_timeout",
        "request_timeout",
        "max_body_bytes",
        "max_response_bytes",
        "max_header_bytes",
        "max_concurrent_requests",
        "admin_rate_limit",
        "admin_rate_period",
    ],
)
def test_application_numeric_limits_reject_string_coercion(field: str) -> None:
    """Operational limits cannot change type through direct model construction."""
    with pytest.raises(ValidationError):
        ApplicationConfig(name="test", **{field: "1"})


@pytest.mark.parametrize("field", ["admin_enabled", "trust_forwarded_headers"])
def test_application_flags_reject_coercion(field: str) -> None:
    """Security and administration flags require actual booleans."""
    with pytest.raises(ValidationError):
        ApplicationConfig(name="test", **{field: "true"})


def test_application_identity_and_proxy_entries_reject_string_coercion() -> None:
    """Application identity and proxy trust policy require actual text values."""
    with pytest.raises(ValidationError):
        ApplicationConfig(name=1)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ApplicationConfig(name="test", environment=1)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ApplicationConfig(name="test", trusted_proxies=(1,))  # type: ignore[arg-type]


def test_strict_application_environment_values_are_decoded_before_validation() -> None:
    config = load_application_config(
        {
            "ORBIT_NAME": "test",
            "ORBIT_REQUEST_TIMEOUT": "15.5",
            "ORBIT_MAX_CONCURRENT_REQUESTS": "250",
            "ORBIT_ADMIN_ENABLED": "true",
        }
    )
    assert config.request_timeout == 15.5
    assert config.max_concurrent_requests == 250
    assert config.admin_enabled is True

    custom = load_config(
        ApplicationConfig,
        prefix="APP_",
        environment={"APP_NAME": "custom", "APP_REQUEST_TIMEOUT": "12.5"},
    )
    assert custom.name == "custom"
    assert custom.request_timeout == 12.5


def test_configuration_snapshots_history_and_structural_diff():
    config = Config(ApplicationConfig(name="test"))
    initial = config.snapshot()
    settings = Settings(database=Database(host="db-a", password="secret"))
    config.register("database", settings)
    current = config.snapshot()

    assert current.version > initial.version
    assert initial.configuration_id == current.configuration_id == config.configuration_id
    assert len(config.history) >= 2
    diff = config.diff(initial)
    assert diff["database.database.host"]["after"] == "db-a"
    assert "secret" not in str(diff)
    with pytest.raises(TypeError):
        current.values["database"] = {}  # type: ignore[index]
    assert current.as_dict()["database"]["database"]["host"] == "db-a"

    with pytest.raises(TypeError, match="ConfigSnapshot"):
        config.diff(object())  # type: ignore[arg-type]
    other = Config(ApplicationConfig(name="other"))
    with pytest.raises(ValueError, match="different configuration"):
        config.diff(other.snapshot())


def test_configuration_history_is_bounded() -> None:
    config = Config(ApplicationConfig(name="test"), history_size=3)
    settings = Settings(database=Database(host="db-a", password="secret"))
    config.register("database", settings)
    config.freeze()
    for host in ("db-b", "db-c", "db-d"):
        config.reload_section("database", Settings(database=Database(host=host, password="x")))
    assert len(config.history) == 3
    assert config.history[-1].values["database"]["database"]["host"] == "db-d"  # type: ignore[index]
    with pytest.raises(ValueError):
        Config(ApplicationConfig(name="test"), history_size=0)
    with pytest.raises(ValueError, match="1,000,000"):
        Config(ApplicationConfig(name="test"), history_size=1_000_001)
    with pytest.raises(ValueError):
        Config(ApplicationConfig(name="test"), history_size=True)
    with pytest.raises(ValueError):
        Config(ApplicationConfig(name="test"), history_size="3")  # type: ignore[arg-type]


def test_configuration_observer_capacity_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configuration reload observers cannot create unbounded callback fan-out."""
    monkeypatch.setattr("orbit.config.config._MAX_CORE_CAPACITY", 1)
    config = Config(ApplicationConfig(name="test"))
    config.subscribe(lambda change: None)
    with pytest.raises(RuntimeError, match="capacity"):
        config.subscribe(lambda change: None)


def test_configuration_section_capacity_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Typed configuration sections cannot retain an unbounded composition graph."""
    monkeypatch.setattr("orbit.config.config._MAX_CORE_CAPACITY", 1)
    config = Config(ApplicationConfig(name="test"))
    config.register("first", Settings(database=Database(host="db-a", password="secret")))
    with pytest.raises(RuntimeError, match="capacity"):
        config.register("second", Settings(database=Database(host="db-b", password="secret")))


def test_configuration_registration_rejects_untyped_sections():
    with pytest.raises(TypeError, match="ApplicationConfig"):
        Config(object())  # type: ignore[arg-type]
    config = Config(ApplicationConfig(name="test"))
    with pytest.raises(TypeError, match="nonempty name"):
        config.register("", Settings(database=Database(host="db-a", password="secret")))
    with pytest.raises(TypeError, match="Pydantic model"):
        config.register("database", {"host": "db-a"})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="identifier-shaped"):
        config.register("Bad Section", Settings(database=Database(host="db-a", password="secret")))
    with pytest.raises(ValueError, match="identifier-shaped"):
        config.register("x" * 64, Settings(database=Database(host="db-a", password="secret")))
    config.register("database", Settings(database=Database(host="db-a", password="secret")))
    with pytest.raises(ValueError, match="frozen"):
        config.reload_section("database", Settings(database=Database(host="db-b", password="x")))


def test_configuration_history_records_validate_identity_and_mapping_keys() -> None:
    snapshot = ConfigSnapshot(0, {"application": {"name": "demo"}})
    change = ConfigChange("database", 1, {"host": "before"}, {"host": "after"})
    assert snapshot.as_dict()["application"]["name"] == "demo"  # type: ignore[index]
    assert change.version == 1

    with pytest.raises(ValueError, match="nonnegative"):
        ConfigSnapshot(-1, {})
    with pytest.raises(ValueError, match="positive"):
        ConfigChange("database", 0, {}, {})
    with pytest.raises(ValueError, match="keys"):
        ConfigSnapshot(1, {1: "not a JSON key"})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="JSON-safe"):
        ConfigSnapshot(1, {"value": object()})
    with pytest.raises(TypeError, match="JSON-safe"):
        ConfigChange("database", 1, {"value": float("nan")}, {})


def test_configuration_snapshots_reject_cycles_and_excessive_nesting() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(ValueError, match="cyclic"):
        ConfigSnapshot(1, cyclic)

    nested: object = "leaf"
    for _ in range(64):
        nested = [nested]
    with pytest.raises(ValueError, match="nested"):
        ConfigSnapshot(1, {"nested": nested})


def test_frozen_configuration_sections_reload_atomically_and_notify():
    config = Config(ApplicationConfig(name="test"))
    settings = Settings(database=Database(host="db-a", password="secret"))
    config.register("database", settings)
    config.freeze()
    changes = []
    config.subscribe(changes.append)

    replacement = Settings(database=Database(host="db-b", password="new-secret"))
    change = config.reload_section("database", replacement)
    assert config.get("database").database.host == "db-b"
    assert change.section == "database"
    assert change.version == config.version
    assert changes == [change]
    assert "new-secret" not in str(change)
    with pytest.raises(TypeError):
        change.after["database"] = {}  # type: ignore[index]


def test_configuration_reloads_are_serialized_across_threads():
    config = Config(ApplicationConfig(name="test"), history_size=32)
    config.register("database", Settings(database=Database(host="db-a", password="secret")))
    config.freeze()

    def reload(index: int) -> int:
        return config.reload_section(
            "database", Settings(database=Database(host=f"db-{index}", password="secret"))
        ).version

    with ThreadPoolExecutor(max_workers=8) as executor:
        versions = list(executor.map(reload, range(20)))
    assert sorted(versions) == list(range(min(versions), max(versions) + 1))
    assert config.version == max(versions)
    assert len(config.history) == config.version


def test_reload_observer_failure_rolls_back_without_new_history():
    config = Config(ApplicationConfig(name="test"))
    settings = Settings(database=Database(host="db-a", password="secret"))
    config.register("database", settings)
    config.freeze()
    version = config.version

    def reject(change):
        raise RuntimeError("service rejected change")

    config.subscribe(reject)
    with pytest.raises(RuntimeError):
        config.reload_section("database", Settings(database=Database(host="db-b", password="x")))
    assert config.version == version
    assert config.get("database").database.host == "db-a"


def test_reload_observer_cancellation_rolls_back_without_new_history():
    config = Config(ApplicationConfig(name="test"))
    config.register("database", Settings(database=Database(host="db-a", password="secret")))
    config.freeze()
    version = config.version

    def cancel(change):
        raise asyncio.CancelledError

    config.subscribe(cancel)
    with pytest.raises(asyncio.CancelledError):
        config.reload_section("database", Settings(database=Database(host="db-b", password="x")))
    assert config.version == version
    assert config.get("database").database.host == "db-a"


def test_reload_observer_cannot_start_a_nested_reload():
    config = Config(ApplicationConfig(name="test"))
    config.register("database", Settings(database=Database(host="db-a", password="secret")))
    config.freeze()
    version = config.version

    def recursive(change):
        config.reload_section("database", Settings(database=Database(host="db-c", password="x")))

    config.subscribe(recursive)
    with pytest.raises(RuntimeError, match="already in progress"):
        config.reload_section("database", Settings(database=Database(host="db-b", password="x")))
    assert config.version == version
    assert config.get("database").database.host == "db-a"


async def test_configuration_watcher_applies_valid_changes_and_retains_good_state(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text('[database]\nhost="db-a"\npassword="secret"\n')
    config = Config(ApplicationConfig(name="test"))
    config.register("database", Settings(database=Database(host="db-a", password="secret")))
    config.freeze()
    watcher = ConfigWatcher(config, "database", Settings, file)
    assert await watcher.run_once() is None
    file.write_text('[database]\nhost="db-b"\npassword="secret"\n')
    change = await watcher.run_once()
    assert change is not None
    assert config.get("database").database.host == "db-b"


async def test_configuration_watcher_detects_in_place_edit_with_same_size_and_mtime(
    tmp_path,
) -> None:
    file = tmp_path / "config.toml"
    original = '[database]\nhost="db-a"\npassword="secret"\n'
    replacement = '[database]\nhost="db-b"\npassword="secret"\n'
    file.write_text(original)
    config = Config(ApplicationConfig(name="test"))
    config.register("database", Settings(database=Database(host="db-a", password="secret")))
    config.freeze()
    watcher = ConfigWatcher(config, "database", Settings, file)
    assert await watcher.run_once() is None
    old_stat = file.stat()
    file.write_text(replacement)
    os.utime(file, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    new_stat = file.stat()
    assert (new_stat.st_size, new_stat.st_ino, new_stat.st_mtime_ns) == (
        old_stat.st_size,
        old_stat.st_ino,
        old_stat.st_mtime_ns,
    )
    change = await watcher.run_once()
    assert change is not None
    assert config.get("database").database.host == "db-b"
    file.write_text("[database]\nhost=\n")
    assert await watcher.run_once() is None
    assert watcher.last_error is not None
    assert config.get("database").database.host == "db-b"


async def test_configuration_watcher_sanitizes_observer_errors(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text('[database]\nhost="db-a"\npassword="secret"\n')
    config = Config(ApplicationConfig(name="test"))
    config.register("database", Settings(database=Database(host="db-a", password="secret")))
    config.freeze()

    def reject(change):
        raise RuntimeError("private-database-password")

    config.subscribe(reject)
    watcher = ConfigWatcher(config, "database", Settings, file)
    assert await watcher.run_once() is None
    file.write_text('[database]\nhost="db-b"\npassword="secret"\n')
    assert await watcher.run_once() is None
    assert watcher.last_error is not None
    assert "private-database-password" not in str(watcher.last_error)
    assert "RuntimeError" in str(watcher.last_error)


async def test_configuration_watcher_detects_atomic_replacement_with_same_size_and_mtime(
    tmp_path,
) -> None:
    file = tmp_path / "config.toml"
    original = '[database]\nhost="db-a"\npassword="secret"\n'
    replacement = '[database]\nhost="db-b"\npassword="secret"\n'
    file.write_text(original)
    config = Config(ApplicationConfig(name="test"))
    config.register("database", Settings(database=Database(host="db-a", password="secret")))
    config.freeze()
    watcher = ConfigWatcher(config, "database", Settings, file)
    assert await watcher.run_once() is None
    old_stat = file.stat()
    candidate = tmp_path / "replacement.toml"
    candidate.write_text(replacement)
    os.utime(candidate, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    os.replace(candidate, file)
    assert file.stat().st_ino != old_stat.st_ino
    change = await watcher.run_once()
    assert change is not None
    assert config.get("database").database.host == "db-b"


async def test_configuration_watcher_does_not_apply_file_changed_during_read(tmp_path, monkeypatch):
    file = tmp_path / "config.toml"
    file.write_text('[database]\nhost="db-a"\npassword="secret"\n')
    config = Config(ApplicationConfig(name="test"))
    config.register("database", Settings(database=Database(host="db-a", password="secret")))
    config.freeze()
    watcher = ConfigWatcher(config, "database", Settings, file)
    assert await watcher.run_once() is None
    file.write_text('[database]\nhost="db-c"\npassword="secret"\n')

    original_load = watcher_module.load_config

    def rewrite_after_read(*args, **kwargs):
        candidate = original_load(*args, **kwargs)
        file.write_text('[database]\nhost="db-b"\npassword="secret"\n')
        return candidate

    monkeypatch.setattr(watcher_module, "load_config", rewrite_after_read)
    assert await watcher.run_once() is None
    assert config.get("database").database.host == "db-a"
    assert watcher.last_error is not None

    monkeypatch.setattr(watcher_module, "load_config", original_load)
    change = await watcher.run_once()
    assert change is not None
    assert config.get("database").database.host == "db-b"


async def test_configuration_watcher_start_stop_is_idempotence_safe(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text('[database]\nhost="db-a"\npassword="secret"\n')
    config = Config(ApplicationConfig(name="test"))
    config.register("database", Settings(database=Database(host="db-a", password="secret")))
    config.freeze()
    watcher = ConfigWatcher(config, "database", Settings, file, interval=0.01)
    await watcher.start()
    with pytest.raises(RuntimeError):
        await watcher.start()
    await watcher.stop()
    await watcher.stop()


async def test_configuration_watcher_can_restart_after_unexpected_task_exit(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text('[database]\nhost="db-a"\npassword="secret"\n')
    config = Config(ApplicationConfig(name="test"))
    config.register("database", Settings(database=Database(host="db-a", password="secret")))
    config.freeze()
    watcher = ConfigWatcher(config, "database", Settings, file, interval=0.01)
    await watcher.start()
    old_task = watcher._task  # noqa: SLF001 - force the failure scenario for this regression.
    assert old_task is not None
    old_task.cancel()
    await asyncio.gather(old_task, return_exceptions=True)
    await watcher.start()
    assert watcher.running
    await watcher.stop()


def test_configuration_watcher_rejects_non_finite_interval(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text('[database]\nhost="db-a"\npassword="secret"\n')
    config = Config(ApplicationConfig(name="test"))
    with pytest.raises(ValueError):
        ConfigWatcher(config, "database", Settings, file, interval=float("nan"))
    with pytest.raises(ValueError):
        ConfigWatcher(config, "database", Settings, file, interval="1")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ConfigWatcher(config, "database", object, file)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ConfigWatcher(config, "database", Settings, str(file))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="64 MiB"):
        ConfigWatcher(config, "database", Settings, file, max_file_bytes=64 * 1024 * 1024 + 1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"prefix": 1},
        {"values": []},
        {"environment": {1: "value"}},
        {"environment": {"ORBIT_NAME": 1}},
        {"environment": {"ORBIT_NAME": None}},
        {"file": "settings.toml"},
        {"model": object},
    ],
)
def test_configuration_loader_rejects_malformed_boundary_inputs_without_leaking_values(kwargs):
    with pytest.raises(ConfigurationError) as error:
        if "model" in kwargs:
            load_config(**kwargs)
        else:
            load_config(Settings, **kwargs)
    assert "value" not in str(error.value)
