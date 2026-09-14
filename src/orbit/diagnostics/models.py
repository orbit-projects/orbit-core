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

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from orbit.application.models import ApplicationSummary


class RequestRecord(BaseModel):
    """Request outcome without raw URL, credentials, request body or event payload."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    request_id: str
    method: str
    status: int = Field(ge=100, le=599)
    duration_seconds: float = Field(ge=0)
    outcome: Literal["completed", "failed", "cancelled", "disconnected"] = "completed"


class LatencyBucket(BaseModel):
    """Cumulative request durations at or below a fixed upper bound, in seconds."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    upper_bound: float = Field(gt=0)
    count: int = Field(ge=0)


class TelemetrySink(Protocol):
    """Nonblocking observer; implementations may enqueue records for an external backend."""

    def record(self, request: RequestRecord) -> None:
        """Receive a completed request record without blocking the event loop."""


class DiagnosticSnapshot(BaseModel):
    """A Core state snapshot with cumulative request metrics and bounded recent activity."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    application: ApplicationSummary
    request_count: int = 0
    error_count: int = 0
    duration_seconds: float = 0
    recent_requests: tuple[RequestRecord, ...] = ()
    latency_buckets: tuple[LatencyBucket, ...] = ()
    status_counts: dict[int, int] = Field(default_factory=dict)
    cancelled_count: int = 0
    disconnected_count: int = 0


__all__ = ["DiagnosticSnapshot", "LatencyBucket", "RequestRecord", "TelemetrySink"]
