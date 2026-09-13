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

from pydantic import BaseModel

from orbit.config.models import ApplicationConfig


class Config:
    """Own typed configuration sections; SecretStr/SecretBytes stay redacted in inspection."""

    def __init__(self, application: ApplicationConfig) -> None:
        self._application = application.model_copy(deep=True)
        self._sections: dict[str, BaseModel] = {}
        self._frozen = False

    @property
    def application(self) -> ApplicationConfig:
        """Return the immutable application configuration."""
        return self._application

    def register(self, name: str, model: BaseModel) -> None:
        """Register an extension's validated settings before startup."""
        if self._frozen or name in self._sections or name == "application":
            raise ValueError("Configuration is frozen or the section already exists.")
        self._sections[name] = model.model_copy(deep=True)

    def get(self, name: str) -> BaseModel:
        """Return an isolated copy of an extension's settings."""
        return self._sections[name].model_copy(deep=True)

    def freeze(self) -> None:
        """End configuration registration."""
        self._frozen = True

    def inspect(self) -> dict[str, object]:
        """Return JSON-safe settings with Pydantic secret values masked."""
        return {
            "application": self._application.model_dump(mode="json"),
            **{key: value.model_dump(mode="json") for key, value in self._sections.items()},
        }


__all__ = ["Config"]
