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
"""ASGI lifespan ownership of startup, request draining and shutdown."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from orbit.asgi.types import Receive, Send

if TYPE_CHECKING:
    from orbit.application import Application
_LOG = logging.getLogger(__name__)


async def handle_lifespan(
    application: Application,
    receive: Receive,
    send: Send,
    *,
    shutdown: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Emit exactly one completion/failure per operation; startup failure terminates lifespan."""
    message = await receive()
    if message["type"] != "lifespan.startup":
        raise RuntimeError("Expected lifespan.startup.")
    try:
        await application.startup()
    except Exception:
        _LOG.exception("Application startup failed")
        await send(
            {"type": "lifespan.startup.failed", "message": "Orbit startup failed; see logs."}
        )
        return
    await send({"type": "lifespan.startup.complete"})
    try:
        message = await receive()
        if message["type"] != "lifespan.shutdown":
            raise RuntimeError("Expected lifespan.shutdown.")
    finally:
        try:
            await (shutdown() if shutdown else application.stop())
        except Exception:
            _LOG.exception("Application shutdown failed")
            await send(
                {"type": "lifespan.shutdown.failed", "message": "Orbit shutdown failed; see logs."}
            )
        else:
            await send({"type": "lifespan.shutdown.complete"})


__all__ = ["handle_lifespan"]
