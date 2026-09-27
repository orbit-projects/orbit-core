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
"""Orbit's framework-owned ASGI boundary and HTTP composition primitives.

The package implements the protocol boundary required by the orchestrator without selecting a
third-party web framework. Provider integrations and business features remain plugin-owned.
"""

from orbit.asgi.application import ASGIApplication
from orbit.asgi.compression import GZipMiddleware
from orbit.asgi.cors import CORSMiddleware
from orbit.asgi.middleware import Middleware, NextHandler
from orbit.asgi.ratelimit import RateLimitMiddleware
from orbit.asgi.request import (
    MAX_BODY_BYTES,
    MAX_HEADER_BYTES,
    MAX_HEADER_COUNT,
    MAX_QUERY_BYTES,
    Request,
)
from orbit.asgi.response import Response

__all__ = [
    "ASGIApplication",
    "CORSMiddleware",
    "GZipMiddleware",
    "MAX_BODY_BYTES",
    "MAX_HEADER_BYTES",
    "MAX_HEADER_COUNT",
    "MAX_QUERY_BYTES",
    "Middleware",
    "NextHandler",
    "RateLimitMiddleware",
    "Request",
    "Response",
]
