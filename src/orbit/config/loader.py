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
"""Typed TOML/environment composition without implicit global state or secret leakage."""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from orbit.config.models import ApplicationConfig
from orbit.errors import ConfigurationError, ErrorCategory, OrbitProblem

T = TypeVar("T", bound=BaseModel)
_MAX_INPUT_VALUES = 100_000
_MAX_INPUT_DEPTH = 64
MAX_CONFIG_PREFIX_LENGTH = 255
MAX_CONFIG_FILE_BYTES = 64 * 1024 * 1024


def _copy_input_value(
    value: object,
    *,
    budget: list[int],
    active: set[int],
    depth: int = 0,
) -> Any:
    """Validate and detach nested input before merge or Pydantic validation work.

    Detaching during the first walk is important for custom mappings: validating one
    iteration and merging a later iteration would allow a mutable source to change after
    the work budget and key checks had already passed.
    """
    budget[0] += 1
    if budget[0] > _MAX_INPUT_VALUES:
        raise ValueError(f"Configuration input cannot exceed {_MAX_INPUT_VALUES:,} values.")
    if not isinstance(value, (Mapping, list, tuple, set, frozenset)):
        return value
    if depth >= _MAX_INPUT_DEPTH:
        raise ValueError(f"Configuration input cannot be nested beyond {_MAX_INPUT_DEPTH} levels.")
    identity = id(value)
    if identity in active:
        raise ValueError("Configuration input cannot contain cyclic references.")
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            copied: dict[str, Any] = {}
            for key, item in value.items():
                if (
                    not isinstance(key, str)
                    or not 1 <= len(key) <= 255
                    or any(ord(character) < 32 or ord(character) == 127 for character in key)
                ):
                    raise ValueError(
                        "Configuration input mapping keys must be bounded printable strings."
                    )
                copied[key] = _copy_input_value(item, budget=budget, active=active, depth=depth + 1)
            return copied
        if isinstance(value, list):
            return [
                _copy_input_value(item, budget=budget, active=active, depth=depth + 1)
                for item in value
            ]
        if isinstance(value, tuple):
            return tuple(
                _copy_input_value(item, budget=budget, active=active, depth=depth + 1)
                for item in value
            )
        if isinstance(value, set):
            return {
                _copy_input_value(item, budget=budget, active=active, depth=depth + 1)
                for item in value
            }
        return frozenset(
            _copy_input_value(item, budget=budget, active=active, depth=depth + 1) for item in value
        )
    finally:
        active.remove(identity)


def _decode_application_scalars(
    data: dict[str, Any], environment: Mapping[str, str], *, prefix: str
) -> None:
    """Decode primitive ORBIT_ values for strict application settings.

    Generic extension settings intentionally retain scalar environment values as strings so
    their Pydantic models own conversion policy. ``ApplicationConfig`` is different: its
    operational limits are strict at the direct model boundary, so primitive environment
    values are decoded before strict validation without weakening extension contracts.
    """
    for name, raw in environment.items():
        parts = name[len(prefix) :].lower().split("__") if name.startswith(prefix) else ()
        if len(parts) != 1 or parts[0] not in data or not isinstance(data[parts[0]], str):
            continue
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, (bool, int, float)) or decoded is None:
            data[parts[0]] = decoded


def load_config(
    model: type[T],
    *,
    values: Mapping[str, Any] | None = None,
    environment: Mapping[str, str] | None = None,
    prefix: str = "ORBIT_",
    file: Path | None = None,
    max_file_bytes: int = 1024 * 1024,
) -> T:
    """Compose TOML, explicit values, then prefixed environment variables (highest priority).

    Nested fields use double underscores, e.g. ORBIT_DATABASE__HOST. JSON objects and
    arrays in environment values are decoded; scalar values remain strings for Pydantic.
    Explicit nested fields override fields from a JSON parent object. Pydantic owns value
    coercion and validation. Unrecognized prefixed fields are rejected by extra='forbid'
    models. Validation errors omit submitted values to avoid exposing secrets.
    """

    def merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
        """Recursively merge nested mappings without mutating the source configuration."""
        for key, value in source.items():
            if isinstance(value, Mapping):
                existing = target.get(key)
                if not isinstance(existing, dict):
                    existing = {}
                    target[key] = existing
                merge(existing, value)
            else:
                target[key] = value

    data: dict[str, Any] = {}
    try:
        if (
            not isinstance(model, type)
            or not issubclass(model, BaseModel)
            or not isinstance(prefix, str)
            or not 1 <= len(prefix) <= MAX_CONFIG_PREFIX_LENGTH
            or any(ord(character) < 32 or ord(character) == 127 for character in prefix)
            or isinstance(max_file_bytes, bool)
            or not isinstance(max_file_bytes, int)
            or not 1 <= max_file_bytes <= MAX_CONFIG_FILE_BYTES
            or (values is not None and not isinstance(values, Mapping))
            or (environment is not None and not isinstance(environment, Mapping))
            or (file is not None and not isinstance(file, Path))
        ):
            raise ValueError(
                "Configuration loader arguments, prefix, or file-size limit are invalid."
            )
        explicit_values = values if values is not None else {}
        supplied_environment = environment if environment is not None else {}
        input_budget = [0]
        input_active: set[int] = set()
        if file is not None:
            with file.open("rb") as stream:
                content = stream.read(max_file_bytes + 1)
            if len(content) > max_file_bytes:
                raise ValueError("Configuration file exceeds size limit.")
            file_values = tomllib.loads(content.decode("utf-8"))
            file_snapshot = _copy_input_value(file_values, budget=input_budget, active=input_active)
            if not isinstance(file_snapshot, dict):
                raise ValueError("Configuration file root must be a mapping.")
            merge(data, file_snapshot)
        explicit_snapshot = _copy_input_value(
            explicit_values, budget=input_budget, active=input_active
        )
        environment_snapshot = _copy_input_value(
            supplied_environment, budget=input_budget, active=input_active
        )
        if not isinstance(explicit_snapshot, dict) or not isinstance(environment_snapshot, dict):
            raise ValueError("Configuration inputs must be mappings.")
        explicit_values = explicit_snapshot
        supplied_environment = environment_snapshot
        if any(
            not isinstance(name, str) or not isinstance(value, str)
            for name, value in supplied_environment.items()
        ):
            raise ValueError("Configuration environment entries must be strings.")
        merge(data, explicit_values)
        seen: set[tuple[str, ...]] = set()
        for name, value in sorted(supplied_environment.items()):
            if not name.startswith(prefix):
                continue
            parts = name[len(prefix) :].lower().split("__")
            if not all(parts):
                raise ValueError("Empty environment field.")
            if tuple(parts) in seen:
                raise ValueError("Ambiguous environment field casing.")
            seen.add(tuple(parts))
            decoded = json.loads(value) if value.lstrip().startswith(("[", "{")) else value
            decoded = _copy_input_value(decoded, budget=input_budget, active=input_active)
            cursor = data
            for part in parts[:-1]:
                existing = cursor.setdefault(part, {})
                if not isinstance(existing, dict):
                    raise ValueError("Conflicting environment fields.")
                cursor = existing
            if isinstance(decoded, Mapping):
                existing = cursor.get(parts[-1])
                if not isinstance(existing, dict):
                    existing = {}
                    cursor[parts[-1]] = existing
                merge(existing, decoded)
            else:
                cursor[parts[-1]] = decoded
        if model is ApplicationConfig:
            _decode_application_scalars(data, supplied_environment, prefix=prefix)
        return model.model_validate(data)
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        fields = (
            [".".join(map(str, error["loc"])) for error in exc.errors(include_input=False)]
            if isinstance(exc, ValidationError)
            else []
        )
        raise ConfigurationError(
            OrbitProblem(
                code="configuration.invalid",
                message="Configuration could not be loaded or validated.",
                category=ErrorCategory.CONFIGURATION,
                context={"fields": fields},
            )
        ) from None


def load_application_config(environment: Mapping[str, str]) -> ApplicationConfig:
    """Load application config from the supplied ORBIT_ environment mapping."""
    return load_config(ApplicationConfig, environment=environment)


__all__ = ["load_application_config", "load_config"]
