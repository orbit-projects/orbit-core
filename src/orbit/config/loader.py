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
        if not prefix or max_file_bytes <= 0:
            raise ValueError("A nonempty prefix and positive file size limit are required.")
        if file is not None:
            with file.open("rb") as stream:
                content = stream.read(max_file_bytes + 1)
            if len(content) > max_file_bytes:
                raise ValueError("Configuration file exceeds size limit.")
            merge(data, tomllib.loads(content.decode("utf-8")))
        merge(data, values or {})
        seen: set[tuple[str, ...]] = set()
        for name, value in sorted((environment or {}).items()):
            if not name.startswith(prefix):
                continue
            parts = name[len(prefix) :].lower().split("__")
            if not all(parts):
                raise ValueError("Empty environment field.")
            if tuple(parts) in seen:
                raise ValueError("Ambiguous environment field casing.")
            seen.add(tuple(parts))
            decoded = json.loads(value) if value.lstrip().startswith(("[", "{")) else value
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
        return model.model_validate(data)
    except (OSError, ValueError, RecursionError) as exc:
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
