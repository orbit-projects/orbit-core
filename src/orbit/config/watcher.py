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
"""Cancellation-safe polling watcher for validated extension configuration files."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from orbit._limits import _MAX_CONFIG_FILE_BYTES, is_finite_number, safe_exception_type_name
from orbit.config.config import _SECTION_NAME, Config, ConfigChange
from orbit.config.loader import _MAX_PREFIX_LENGTH, load_config

T = TypeVar("T", bound=BaseModel)
_Fingerprint = tuple[int, int, int, str]


class ConfigWatcher(Generic[T]):
    """Watch one TOML section and apply only validated, observer-approved replacements."""

    def __init__(
        self,
        config: Config,
        section: str,
        model: type[T],
        file: Path,
        *,
        interval: float = 1.0,
        values: Mapping[str, Any] | None = None,
        environment: Mapping[str, str] | None = None,
        prefix: str = "ORBIT_",
        max_file_bytes: int = 1024 * 1024,
    ) -> None:
        if not isinstance(config, Config):
            raise TypeError("ConfigWatcher requires a Config owner.")
        if not isinstance(section, str) or _SECTION_NAME.fullmatch(section) is None:
            raise ValueError("Configuration watcher sections must be lowercase identifiers.")
        if (
            isinstance(interval, bool)
            or not isinstance(interval, (int, float))
            or not is_finite_number(interval)
            or interval <= 0
        ):
            raise ValueError("interval must be finite and positive.")
        if (
            isinstance(max_file_bytes, bool)
            or not isinstance(max_file_bytes, int)
            or not 1 <= max_file_bytes <= _MAX_CONFIG_FILE_BYTES
        ):
            raise ValueError("max_file_bytes must be between 1 and 64 MiB.")
        self._config = config
        self._section = section
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            raise TypeError("model must be a Pydantic model class.")
        if not isinstance(file, Path):
            raise TypeError("file must be a pathlib.Path.")
        if (
            not isinstance(prefix, str)
            or not 1 <= len(prefix) <= _MAX_PREFIX_LENGTH
            or any(ord(character) < 32 or ord(character) == 127 for character in prefix)
        ):
            raise ValueError("prefix must be a bounded printable string.")
        self._model = model
        self._file = file
        self._interval = interval
        self._values = values
        self._environment = environment
        self._prefix = prefix
        self._max_file_bytes = max_file_bytes
        self._fingerprint: _Fingerprint | None = None
        self._task: asyncio.Task[None] | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._last_error: Exception | None = None

    @property
    def running(self) -> bool:
        """Return whether the watcher owns a polling task."""
        return self._task is not None and not self._task.done()

    @property
    def last_error(self) -> Exception | None:
        """Return a sanitized summary of the latest load or observer error."""
        return self._last_error

    @staticmethod
    def _safe_error(error: Exception) -> RuntimeError:
        """Retain failure type without exposing paths, values, or provider messages."""
        return RuntimeError(f"Configuration watcher failed ({safe_exception_type_name(error)}).")

    def _fingerprint_file(self) -> _Fingerprint:
        """Read a bounded content digest so same-stat in-place edits cannot be missed."""
        stat = self._file.stat()
        digest = hashlib.sha256()
        remaining = self._max_file_bytes + 1
        with self._file.open("rb") as stream:
            while remaining:
                chunk = stream.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                digest.update(chunk)
                remaining -= len(chunk)
        return stat.st_mtime_ns, stat.st_size, stat.st_ino, digest.hexdigest()

    async def run_once(self) -> ConfigChange | None:
        """Poll once; unchanged files and the initial observation do not trigger a reload."""
        try:
            fingerprint = self._fingerprint_file()
        except Exception as exc:
            self._last_error = self._safe_error(exc)
            return None
        if self._fingerprint == fingerprint:
            return None
        if self._fingerprint is None:
            # Validate the initial observation for operator visibility, but do not mutate the
            # already-composed section until a later file change is observed. A file that changes
            # while validation runs remains uncommitted and will be retried on the next poll.
            try:
                load_config(
                    self._model,
                    file=self._file,
                    values=self._values,
                    environment=self._environment,
                    prefix=self._prefix,
                    max_file_bytes=self._max_file_bytes,
                )
                if self._fingerprint_file() != fingerprint:
                    self._last_error = RuntimeError("Configuration file changed during read.")
                else:
                    self._last_error = None
            except Exception as exc:
                self._last_error = self._safe_error(exc)
            self._fingerprint = fingerprint
            return None
        try:
            candidate = load_config(
                self._model,
                file=self._file,
                values=self._values,
                environment=self._environment,
                prefix=self._prefix,
                max_file_bytes=self._max_file_bytes,
            )
            current_fingerprint = self._fingerprint_file()
            if current_fingerprint != fingerprint:
                self._last_error = RuntimeError("Configuration file changed during read.")
                return None
            change = self._config.reload_section(self._section, candidate)
        except Exception as exc:
            self._last_error = self._safe_error(exc)
            return None
        self._fingerprint = fingerprint
        self._last_error = None
        return change

    async def start(self) -> None:
        """Start one polling task; the current file is observed without an initial mutation."""
        async with self._lifecycle_lock:
            if self.running:
                raise RuntimeError("Configuration watcher is already running.")
            previous = self._task
            if previous is not None:
                # A completed task may still be executing its ``finally`` block. Join it
                # before replacing the handle so stale cleanup cannot clear the new task.
                await asyncio.gather(previous, return_exceptions=True)
                if self._task is previous:
                    self._task = None
            self._stop = asyncio.Event()
            task = asyncio.create_task(self._run(), name=f"orbit:config:{self._section}")
            self._task = task

    async def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._last_error = self._safe_error(exc)
                try:
                    async with asyncio.timeout(self._interval):
                        await self._stop.wait()
                except TimeoutError:
                    continue
        finally:
            current = asyncio.current_task()
            if self._task is current:
                self._task = None

    async def stop(self) -> None:
        """Stop polling and await task cleanup even when the caller is cancelled."""
        async with self._lifecycle_lock:
            task = self._task
            if task is None:
                return
            self._stop.set()
            cancelled = False
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError


__all__ = ["ConfigWatcher"]
