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
"""Runtime inspection models."""

from __future__ import annotations

from enum import StrEnum
from ipaddress import ip_address

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from orbit.lifecycle import LifecyclePhase


class HostServer(StrEnum):
    """Supported Orbit host process implementations."""

    UVICORN = "uvicorn"
    GUNICORN = "gunicorn"


class HostingConfig(BaseModel):
    """Validated shared hosting policy for Uvicorn and Gunicorn deployments."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, validate_default=True
    )

    server: HostServer = HostServer.UVICORN
    host: StrictStr = Field(default="127.0.0.1", min_length=1, max_length=255)
    port: StrictInt = Field(default=8000, ge=1, le=65535)
    workers: StrictInt = Field(default=1, ge=1, le=1024)
    reload: StrictBool = False
    graceful_timeout: StrictInt = Field(default=30, ge=1, le=3600)
    worker_timeout: StrictInt = Field(default=30, ge=1, le=3600)
    keep_alive: StrictInt = Field(default=5, ge=0, le=3600)
    max_requests: StrictInt = Field(default=0, ge=0, le=10_000_000)
    max_requests_jitter: StrictInt = Field(default=0, ge=0, le=1_000_000)

    @field_validator(
        "port",
        "workers",
        "graceful_timeout",
        "worker_timeout",
        "keep_alive",
        "max_requests",
        "max_requests_jitter",
        mode="before",
    )
    @classmethod
    def reject_boolean_limits(cls, value: object) -> object:
        """Reject booleans where a numeric host limit is required."""
        if isinstance(value, bool):
            raise ValueError("hosting numeric limits must be integers, not booleans.")
        return value

    @field_validator("server", mode="before")
    @classmethod
    def reject_non_text_server(cls, value: object) -> object:
        """Reject byte or numeric coercion before selecting a host process."""
        if not isinstance(value, (str, HostServer)):
            raise ValueError("server must be a Uvicorn or Gunicorn text value.")
        return value

    @field_validator("host")
    @classmethod
    def validate_host(cls, value: str) -> str:
        """Reject control characters and malformed IPv6 bind literals."""
        if any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        ):
            raise ValueError("host must not contain whitespace or control characters.")
        bracketed = value.startswith("[") or value.endswith("]")
        if bracketed and not (value.startswith("[") and value.endswith("]")):
            raise ValueError("bracketed hosts must contain a complete IPv6 literal.")
        candidate = value[1:-1] if bracketed else value
        if bracketed and ":" not in candidate:
            raise ValueError("bracketed hosts must contain an IPv6 literal.")
        if ":" in candidate:
            try:
                ip_address(candidate)
            except ValueError as exc:
                raise ValueError("IPv6 hosts must be valid address literals.") from exc
        return value

    @property
    def bind(self) -> str:
        """Return a host:port bind string with IPv6 literals bracketed for host parsers."""
        host = self.host
        if ":" in host and not (host.startswith("[") and host.endswith("]")):
            host = f"[{host}]"
        return f"{host}:{self.port}"

    @model_validator(mode="after")
    def validate_server_policy(self) -> HostingConfig:
        """Enforce incompatible reload, worker, and worker-recycling combinations."""
        if self.server is HostServer.GUNICORN and self.reload:
            raise ValueError("Gunicorn hosting does not support source reload.")
        if self.server is HostServer.UVICORN and self.reload and self.workers > 1:
            raise ValueError("Uvicorn source reload cannot be combined with multiple workers.")
        if self.max_requests == 0 and self.max_requests_jitter:
            raise ValueError("max_requests_jitter requires max_requests to be enabled.")
        if self.max_requests_jitter > self.max_requests:
            raise ValueError("max_requests_jitter cannot exceed max_requests.")
        return self


class RuntimeInfo(BaseModel):
    """The stable, inspectable identity and aggregate counts of an Orbit runtime.

    Runtime information is an operator-facing contract, not an internal debug dictionary.
    The counters are therefore non-negative strict integers and the application name follows
    the same bounded identifier policy as :class:`~orbit.config.ApplicationConfig`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    application_name: StrictStr = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    phase: LifecyclePhase
    service_count: StrictInt = Field(default=0, ge=0)
    task_count: StrictInt = Field(default=0, ge=0)
    failed_task_count: StrictInt = Field(default=0, ge=0)
    child_count: StrictInt = Field(default=0, ge=0)
    hosting: HostingConfig = HostingConfig()


__all__ = ["HostServer", "HostingConfig", "RuntimeInfo"]
