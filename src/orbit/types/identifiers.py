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
"""UUID-backed domain identifiers distinguish entity types during static checking."""

from typing import NewType
from uuid import UUID, uuid4

ApplicationId = NewType("ApplicationId", UUID)
EventId = NewType("EventId", UUID)
PluginId = NewType("PluginId", UUID)
RouteId = NewType("RouteId", UUID)
ServiceId = NewType("ServiceId", UUID)
RequestId = NewType("RequestId", UUID)
ProviderId = NewType("ProviderId", UUID)


def new_application_id() -> ApplicationId:
    """Create an application identity."""
    return ApplicationId(uuid4())


def new_event_id() -> EventId:
    """Create an event identity."""
    return EventId(uuid4())


def new_plugin_id() -> PluginId:
    """Create a plugin identity."""
    return PluginId(uuid4())


def new_route_id() -> RouteId:
    """Create a route identity."""
    return RouteId(uuid4())


def new_service_id() -> ServiceId:
    """Create a service identity."""
    return ServiceId(uuid4())


__all__ = [
    "ApplicationId",
    "EventId",
    "PluginId",
    "ProviderId",
    "RequestId",
    "RouteId",
    "ServiceId",
    "new_application_id",
    "new_event_id",
    "new_plugin_id",
    "new_route_id",
    "new_service_id",
]
