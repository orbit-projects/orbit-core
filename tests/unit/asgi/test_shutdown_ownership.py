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
"""Runtime shutdown owns outstanding requests even if the host task is cancelled."""

import asyncio

import pytest

from orbit import Application, ApplicationConfig
from orbit.asgi import ASGIApplication, Response
from orbit.asgi.lifespan import handle_lifespan
from orbit.lifecycle import LifecyclePhase


async def test_lifespan_startup_acknowledgement_failure_still_closes_application():
    app = Application(ApplicationConfig(name="acknowledgement"))

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        raise OSError("host disconnected")

    with pytest.raises(OSError):
        await handle_lifespan(app, receive, send)
    assert app.lifecycle.phase is LifecyclePhase.STOPPED


def test_middleware_registration_validates_contract_and_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Middleware composition rejects malformed hooks and excessive pipeline size early."""
    app = Application(ApplicationConfig(name="middleware-contract"))
    runtime = ASGIApplication(app)
    with pytest.raises(TypeError, match="callable"):
        runtime.add_middleware(object())  # type: ignore[arg-type]

    class InvalidHooks:
        async def __call__(self, request, next_handler):
            return await next_handler(request)

        startup = True

    with pytest.raises(TypeError, match="hooks"):
        runtime.add_middleware(InvalidHooks())

    monkeypatch.setattr("orbit.asgi.application._MAX_CORE_CAPACITY", 1)
    runtime.add_middleware(lambda request, next_handler: next_handler(request))
    with pytest.raises(RuntimeError, match="capacity"):
        runtime.add_middleware(lambda request, next_handler: next_handler(request))


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [None, {}, {"type": "http.request"}])
async def test_malformed_startup_lifespan_messages_are_protocol_errors(message):
    app = Application(ApplicationConfig(name="malformed-startup"))

    async def receive():
        return message

    async def send(message):
        raise AssertionError("startup must not send a completion frame")

    with pytest.raises(RuntimeError, match="Expected lifespan.startup"):
        await handle_lifespan(app, receive, send)
    assert app.lifecycle.phase is LifecyclePhase.CREATED


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [None, {}, {"type": "http.request"}])
async def test_malformed_shutdown_lifespan_messages_stop_the_application(message):
    app = Application(ApplicationConfig(name="malformed-shutdown"))
    messages = iter([{"type": "lifespan.startup"}, message])

    async def receive():
        return next(messages)

    async def send(message):
        return None

    with pytest.raises(RuntimeError, match="Expected lifespan.shutdown"):
        await handle_lifespan(app, receive, send)
    assert app.lifecycle.phase is LifecyclePhase.STOPPED


async def test_shutdown_cancellation_waits_for_request_resource_cleanup():
    app = Application(ApplicationConfig(name="drain", lifecycle_timeout=0.01))
    runtime = ASGIApplication(app)
    entered = asyncio.Event()
    cleaned = []

    @app.router.route("/", method="GET", name="root")
    async def route(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.append(True)
        return Response.text("unreachable")

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        return None

    await app.startup()
    request = asyncio.create_task(
        runtime({"type": "http", "method": "GET", "path": "/"}, receive, send)
    )
    await entered.wait()
    shutdown = asyncio.create_task(runtime.shutdown())
    await asyncio.sleep(0)
    shutdown.cancel()
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    await asyncio.gather(request, return_exceptions=True)
    assert cleaned == [True]
    assert app.lifecycle.phase is LifecyclePhase.STOPPED
    assert app.diagnostics.collect(app).cancelled_count == 1
    await runtime.shutdown()


async def test_shutdown_deadline_does_not_wait_for_request_that_suppresses_cancellation():
    app = Application(ApplicationConfig(name="stubborn-request", lifecycle_timeout=0.01))
    runtime = ASGIApplication(app)
    entered = asyncio.Event()
    release = asyncio.Event()

    @app.router.route("/", method="GET", name="root")
    async def route(request):
        entered.set()
        while not release.is_set():
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                continue
        return Response.text("done")

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        return None

    await app.startup()
    request = asyncio.create_task(
        runtime({"type": "http", "method": "GET", "path": "/"}, receive, send)
    )
    await entered.wait()
    with pytest.raises(ExceptionGroup, match="cleanup failed"):
        await asyncio.wait_for(runtime.shutdown(), timeout=0.2)
    assert not request.done()

    release.set()
    request.cancel()
    await asyncio.gather(request, return_exceptions=True)
    await app.stop()


async def test_middleware_shutdown_hook_is_bounded_by_lifecycle_timeout():
    app = Application(ApplicationConfig(name="middleware-timeout", lifecycle_timeout=0.01))
    runtime = ASGIApplication(app)

    class StuckMiddleware:
        async def shutdown(self):
            await asyncio.Event().wait()

        async def __call__(self, request, next_handler):
            return await next_handler(request)

    runtime.add_middleware(StuckMiddleware())
    await app.startup()
    with pytest.raises(ExceptionGroup, match="Middleware shutdown failed"):
        await asyncio.wait_for(runtime.shutdown(), timeout=0.2)
    assert app.lifecycle.phase is LifecyclePhase.STOPPED


async def test_application_shutdown_failure_is_reported_with_middleware_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown must retain middleware and application cleanup failures together."""
    app = Application(ApplicationConfig(name="combined-shutdown-failure"))
    runtime = ASGIApplication(app)
    middleware_failure = RuntimeError("middleware cleanup failed")
    application_failure = RuntimeError("application cleanup failed")

    class FailingMiddleware:
        async def shutdown(self) -> None:
            raise middleware_failure

        async def __call__(self, request, next_handler):
            return await next_handler(request)

    runtime.add_middleware(FailingMiddleware())

    async def failing_stop() -> None:
        raise application_failure

    monkeypatch.setattr(app, "stop", failing_stop)
    with pytest.raises(ExceptionGroup, match="Application shutdown failed") as raised:
        await runtime._shutdown_middleware()

    assert raised.value.exceptions == (middleware_failure, application_failure)


async def test_cancelled_middleware_shutdown_does_not_skip_application_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancellation-signalling hook is aggregated only after application cleanup runs."""
    app = Application(ApplicationConfig(name="cancelled-shutdown-hook"))
    runtime = ASGIApplication(app)
    cleaned = False

    class CancelledMiddleware:
        async def shutdown(self) -> None:
            raise asyncio.CancelledError

        async def __call__(self, request, next_handler):
            return await next_handler(request)

    runtime.add_middleware(CancelledMiddleware())

    original_stop = app.stop

    async def tracked_stop() -> None:
        nonlocal cleaned
        cleaned = True
        await original_stop()

    monkeypatch.setattr(app, "stop", tracked_stop)
    await app.startup()
    with pytest.raises(BaseExceptionGroup, match="Middleware shutdown failed"):
        await runtime._shutdown_middleware()
    assert cleaned
    assert app.lifecycle.phase is LifecyclePhase.STOPPED


async def test_cancellation_resistant_middleware_shutdown_is_detached():
    """A hook that suppresses cancellation cannot hold worker shutdown indefinitely."""
    app = Application(ApplicationConfig(name="stubborn-middleware", lifecycle_timeout=0.01))
    runtime = ASGIApplication(app)
    started = asyncio.Event()
    finished = asyncio.Event()
    release = asyncio.Event()

    class StubbornMiddleware:
        async def shutdown(self):
            started.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    continue
            finished.set()

        async def __call__(self, request, next_handler):
            return await next_handler(request)

    runtime.add_middleware(StubbornMiddleware())
    await app.startup()
    with pytest.raises(ExceptionGroup, match="Middleware shutdown failed"):
        await asyncio.wait_for(runtime.shutdown(), timeout=0.2)
    assert started.is_set()
    assert not finished.is_set()
    assert app.lifecycle.phase is LifecyclePhase.STOPPED

    release.set()
    await asyncio.wait_for(finished.wait(), timeout=0.2)
