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
"""Namespaced error codes emitted by Orbit Core."""

CONTAINER_CIRCULAR_DEPENDENCY = "container.circular-dependency"
CONTAINER_DUPLICATE_PROVIDER = "container.duplicate-provider"
CONTAINER_PROVIDER_NOT_FOUND = "container.provider-not-found"
LIFECYCLE_INVALID_TRANSITION = "lifecycle.invalid-transition"
ROUTING_DUPLICATE_ROUTE = "routing.duplicate-route"
ROUTING_ROUTE_NOT_FOUND = "routing.route-not-found"
SERVICES_DUPLICATE_SERVICE = "services.duplicate-service"
SERVICES_MISSING_DEPENDENCY = "services.missing-dependency"
SERVICES_DEPENDENCY_CYCLE = "services.dependency-cycle"

__all__ = [name for name in globals() if name.isupper()]
