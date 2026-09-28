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
"""Application and extension configuration with validated models and redacted inspection."""

import re
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from threading import RLock
from types import MappingProxyType
from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel

from orbit._limits import _MAX_CORE_CAPACITY, is_finite_number
from orbit.config.models import ApplicationConfig
from orbit.types import ConfigurationId, new_configuration_id

_SECTION_NAME = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")
_MAX_CONFIG_HISTORY = 1_000_000
_MAX_CONFIG_DEPTH = 64
_MAX_CONFIG_VALUES = 100_000
_MAX_CONFIG_KEY_LENGTH = 255


@dataclass(frozen=True)
class ConfigSnapshot:
    """Validated, immutable JSON-safe configuration view captured at one version."""

    version: int
    values: Mapping[str, object]
    configuration_id: ConfigurationId = field(default_factory=new_configuration_id)

    def __post_init__(self) -> None:
        """Validate snapshot identity and version before freezing nested configuration values."""
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 0:
            raise ValueError("Configuration snapshot versions must be nonnegative integers.")
        if not isinstance(self.configuration_id, UUID):
            raise TypeError("Configuration snapshots require a UUID-backed identity.")
        if not isinstance(self.values, Mapping):
            raise TypeError("Configuration snapshot values must be a mapping.")
        # Let _freeze walk the caller mapping directly so its recursive work budget is enforced
        # before a custom mapping can be fully materialized by dict(self.values).
        object.__setattr__(self, "values", _freeze(self.values))

    def as_dict(self) -> dict[str, object]:
        """Return a detached mutable copy for serialization or comparison."""
        return cast(dict[str, object], _thaw(self.values))


def _freeze(
    value: object,
    *,
    active: set[int] | None = None,
    value_count: list[int] | None = None,
    depth: int = 0,
) -> object:
    """Freeze one JSON-safe configuration value with bounded recursive work."""
    if active is None:
        active = set()
    if value_count is None:
        value_count = [0]
    value_count[0] += 1
    if value_count[0] > _MAX_CONFIG_VALUES:
        raise ValueError(f"Configuration values cannot exceed {_MAX_CONFIG_VALUES:,} items.")
    if isinstance(value, (Mapping, list, tuple)):
        if depth >= _MAX_CONFIG_DEPTH:
            raise ValueError(
                f"Configuration values cannot be nested beyond {_MAX_CONFIG_DEPTH} levels."
            )
        identity = id(value)
        if identity in active:
            raise ValueError("Configuration values cannot contain cyclic references.")
        active.add(identity)
        try:
            if isinstance(value, Mapping):
                frozen: dict[str, object] = {}
                for key, item in value.items():
                    if (
                        not isinstance(key, str)
                        or not 1 <= len(key) <= _MAX_CONFIG_KEY_LENGTH
                        or any(ord(character) < 32 or ord(character) == 127 for character in key)
                    ):
                        raise ValueError(
                            "Configuration snapshot mapping keys must be bounded printable strings."
                        )
                    if key in frozen:
                        raise ValueError("Configuration snapshot mapping keys must be unique.")
                    frozen[key] = _freeze(
                        item, active=active, value_count=value_count, depth=depth + 1
                    )
                return MappingProxyType(frozen)
            return tuple(
                _freeze(item, active=active, value_count=value_count, depth=depth + 1)
                for item in value
            )
        finally:
            active.remove(identity)
    if value is None or isinstance(value, (bool, str)):
        return value
    if is_finite_number(value):
        return value
    raise TypeError("Configuration snapshots require JSON-safe scalar values.")


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class ConfigChange:
    """Immutable, redacted description of one accepted section replacement.

    The nested before/after values are frozen as well as the dataclass fields, so an
    observer cannot rewrite an audit record after it has been published.
    """

    section: str
    version: int
    before: object
    after: object

    def __post_init__(self) -> None:
        """Validate change identity and protect nested values from post-publication mutation."""
        if _SECTION_NAME.fullmatch(self.section) is None:
            raise ValueError("Configuration change sections must be lowercase identifiers.")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("Configuration change versions must be positive integers.")
        object.__setattr__(self, "before", _freeze(self.before))
        object.__setattr__(self, "after", _freeze(self.after))


ConfigObserver = Callable[[ConfigChange], None]


class Config:
    """Own typed configuration sections; SecretStr/SecretBytes stay redacted in inspection."""

    def __init__(self, application: ApplicationConfig, *, history_size: int = 1000) -> None:
        if not isinstance(application, ApplicationConfig):
            raise TypeError("Config requires an ApplicationConfig instance.")
        if (
            isinstance(history_size, bool)
            or not isinstance(history_size, int)
            or not 1 <= history_size <= _MAX_CONFIG_HISTORY
        ):
            raise ValueError("history_size must be between 1 and 1,000,000.")
        self._configuration_id = new_configuration_id()
        self._application = application.model_copy(deep=True)
        self._lock = RLock()
        self._sections: dict[str, BaseModel] = {}
        self._frozen = False
        self._version = 0
        self._history: deque[ConfigSnapshot] = deque(maxlen=history_size)
        self._observers: list[ConfigObserver] = []
        self._reload_in_progress = False
        self._record_snapshot()

    @property
    def configuration_id(self) -> ConfigurationId:
        """Return the stable identity shared by this owner and all of its snapshots."""
        return self._configuration_id

    @property
    def application(self) -> ApplicationConfig:
        """Return the immutable application configuration."""
        with self._lock:
            return self._application.model_copy(deep=True)

    def register(self, name: str, model: BaseModel) -> None:
        """Register an extension's validated settings before startup."""
        if not isinstance(name, str) or not name or not isinstance(model, BaseModel):
            raise TypeError("Configuration sections require a nonempty name and Pydantic model.")
        if _SECTION_NAME.fullmatch(name) is None:
            raise ValueError(
                "Configuration section names must be lowercase identifier-shaped strings."
            )
        with self._lock:
            if self._frozen or name in self._sections or name == "application":
                raise ValueError("Configuration is frozen or the section already exists.")
            if len(self._sections) >= _MAX_CORE_CAPACITY:
                raise RuntimeError("Configuration section capacity reached.")
            self._sections[name] = model.model_copy(deep=True)
            self._record_snapshot()

    def get(self, name: str) -> BaseModel:
        """Return an isolated copy of an extension's settings."""
        with self._lock:
            return self._sections[name].model_copy(deep=True)

    def freeze(self) -> None:
        """End configuration registration."""
        with self._lock:
            self._frozen = True
            self._record_snapshot()

    def subscribe(self, observer: ConfigObserver) -> None:
        """Register a synchronous reload observer during composition or operation."""
        if not callable(observer):
            raise TypeError("Configuration observer must be callable.")
        with self._lock:
            if len(self._observers) >= _MAX_CORE_CAPACITY:
                raise RuntimeError("Configuration observer capacity reached.")
            self._observers.append(observer)

    def reload_section(self, name: str, model: BaseModel) -> ConfigChange:
        """Atomically replace a registered section with a validated model.

        Reloading is allowed after freeze for extension settings only. Observers are called
        before the replacement commits; an observer exception restores the prior section and
        leaves version and audit history unchanged. Observers cannot recursively initiate another
        reload while the current replacement is awaiting approval.
        """
        with self._lock:
            if not self._frozen:
                raise ValueError("Configuration must be frozen before a section can be reloaded.")
            if name == "application" or name not in self._sections:
                raise KeyError(name)
            if self._reload_in_progress:
                raise RuntimeError("Configuration reload is already in progress.")
            if not isinstance(model, BaseModel):
                raise TypeError("Configuration sections must be Pydantic models.")
            previous = self._sections[name]
            if type(model) is not type(previous):
                raise TypeError(
                    "Reloaded configuration must use the registered section model type."
                )
            candidate = model.model_copy(deep=True)
            before = previous.model_dump(mode="json")
            after = candidate.model_dump(mode="json")
            change = ConfigChange(name, self._version + 1, before, after)
            self._sections[name] = candidate
            self._reload_in_progress = True
            try:
                for observer in tuple(self._observers):
                    observer(change)
            except BaseException:
                self._sections[name] = previous
                raise
            finally:
                self._reload_in_progress = False
            self._record_snapshot()
            return change

    @property
    def version(self) -> int:
        """Return the monotonically increasing configuration version."""
        with self._lock:
            return self._version

    @property
    def history(self) -> tuple[ConfigSnapshot, ...]:
        """Return detached snapshots retained for the lifetime of this configuration."""
        with self._lock:
            return tuple(self._history)

    def snapshot(self) -> ConfigSnapshot:
        """Capture the current redacted configuration without exposing model objects."""
        with self._lock:
            return ConfigSnapshot(self._version, self.inspect(), self._configuration_id)

    def diff(self, previous: ConfigSnapshot) -> dict[str, dict[str, object]]:
        """Return changed leaf paths from an owned snapshot to current configuration.

        A snapshot is an identity-bound view of one :class:`Config` owner. Rejecting
        snapshots from another owner prevents an operational diff from silently
        comparing unrelated applications that happen to expose similarly shaped data.
        """
        if not isinstance(previous, ConfigSnapshot):
            raise TypeError("Configuration diffs require a ConfigSnapshot.")
        if previous.configuration_id != self._configuration_id:
            raise ValueError("Cannot diff a snapshot from a different configuration.")
        with self._lock:
            current = self.inspect()
        changes: dict[str, dict[str, object]] = {}

        def walk(before: Any, after: Any, path: str) -> None:
            """Record changed leaf paths while preserving nested mapping structure."""
            if isinstance(before, Mapping) or isinstance(after, Mapping):
                before = before if isinstance(before, Mapping) else {}
                after = after if isinstance(after, Mapping) else {}
                for key in sorted(set(before) | set(after)):
                    walk(before.get(key), after.get(key), f"{path}.{key}" if path else key)
                return
            if before != after:
                changes[path] = {"before": before, "after": after}

        walk(dict(previous.values), current, "")
        return changes

    def _record_snapshot(self) -> None:
        self._version += 1
        self._history.append(ConfigSnapshot(self._version, self.inspect(), self._configuration_id))

    def inspect(self) -> dict[str, object]:
        """Return JSON-safe settings with Pydantic secret values masked."""
        with self._lock:
            return {
                "application": self._application.model_dump(mode="json"),
                **{key: value.model_dump(mode="json") for key, value in self._sections.items()},
            }


__all__ = ["Config", "ConfigChange", "ConfigSnapshot", "ConfigObserver"]
