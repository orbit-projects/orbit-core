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
"""Payload-free runtime diagnostics and telemetry extension contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from orbit._immutability import freeze_value
from orbit.application.models import ApplicationSummary
from orbit.types import RequestId


class RequestRecord(BaseModel):
    """Request outcome with typed identity and without raw URL, credentials, or request payload."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, validate_default=True
    )
    request_id: RequestId
    method: StrictStr = Field(pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,32}$")
    status: StrictInt = Field(ge=100, le=599)
    duration_seconds: StrictFloat = Field(ge=0)
    outcome: Literal["completed", "failed", "cancelled", "disconnected"] = "completed"


class LatencyBucket(BaseModel):
    """Cumulative request durations at or below a fixed upper bound, in seconds."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, validate_default=True
    )
    upper_bound: StrictFloat = Field(gt=0)
    count: StrictInt = Field(ge=0)


class TelemetrySink(Protocol):
    """Nonblocking observer; implementations may enqueue records for an external backend."""

    def record(self, request: RequestRecord) -> None:
        """Receive a completed request record without blocking the event loop."""


class DiagnosticSnapshot(BaseModel):
    """A Core state snapshot with cumulative request metrics and bounded recent activity."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, validate_default=True
    )
    application: ApplicationSummary
    request_count: StrictInt = Field(default=0, ge=0)
    error_count: StrictInt = Field(default=0, ge=0)
    duration_seconds: StrictFloat = Field(default=0.0, ge=0)
    recent_requests: tuple[RequestRecord, ...] = ()
    latency_buckets: tuple[LatencyBucket, ...] = ()
    status_counts: dict[int, int] = Field(default_factory=dict)
    cancelled_count: StrictInt = Field(default=0, ge=0)
    disconnected_count: StrictInt = Field(default=0, ge=0)

    @field_validator("status_counts", mode="before")
    @classmethod
    def validate_status_counts(cls, value: object) -> dict[int, int]:
        """Require strict HTTP status/count pairs before Pydantic can coerce them."""
        if not isinstance(value, Mapping):
            raise TypeError("Diagnostic status counts must be a mapping.")
        if len(value) > 500:
            raise ValueError("Diagnostic status counts cannot contain more than 500 statuses.")
        normalized: dict[int, int] = {}
        for status, count in value.items():
            if (
                isinstance(status, bool)
                or not isinstance(status, int)
                or not 100 <= status <= 599
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
            ):
                raise ValueError("Diagnostic status counts require valid nonnegative HTTP counts.")
            normalized[status] = count
        return normalized

    @model_validator(mode="after")
    def validate_totals(self) -> DiagnosticSnapshot:
        """Keep aggregate totals coherent with per-status and terminal-outcome counters."""
        if sum(self.status_counts.values()) != self.request_count:
            raise ValueError("Diagnostic status counts must sum to request_count.")
        if self.error_count > self.request_count:
            raise ValueError("Diagnostic error_count cannot exceed request_count.")
        if self.cancelled_count + self.disconnected_count > self.request_count:
            raise ValueError("Diagnostic terminal outcome counts cannot exceed request_count.")
        return self

    def model_post_init(self, __context: object) -> None:
        """Freeze nested status counts so collected diagnostics cannot be rewritten."""
        object.__setattr__(self, "status_counts", freeze_value(dict(self.status_counts)))


__all__ = ["DiagnosticSnapshot", "LatencyBucket", "RequestRecord", "TelemetrySink"]
