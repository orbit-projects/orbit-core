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
"""The common hosting facade for application orchestration and authenticated ASGI."""

from orbit.application import Application
from orbit.asgi import ASGIApplication
from orbit.runtime.models import RuntimeInfo
from orbit.security.contracts import Authenticator


class Runtime:
    """Expose an application's shared router and optional authentication provider."""

    def __init__(
        self, application: Application, *, authenticator: Authenticator | None = None
    ) -> None:
        self.application = application
        self.asgi = ASGIApplication(application, authenticator=authenticator)

    @property
    def info(self) -> RuntimeInfo:
        """Read current lifecycle state, without a second runtime state machine."""
        return RuntimeInfo(
            application_name=self.application.config.application.name,
            phase=self.application.lifecycle.phase,
        )


__all__ = ["Runtime"]
