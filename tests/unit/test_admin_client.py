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
"""Tests for the remote admin client contract."""

import asyncio
import threading

import pytest

from orbit.admin import AdminClient, AdminClientError, AdminHTTPResponse


class FakeTransport:
    def __init__(self, response: AdminHTTPResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    async def request(self, method, path, *, headers, body=None):
        self.calls.append((method, path, dict(headers)))
        return self.response


class MutatingTransport:
    def __init__(self) -> None:
        self.headers_seen: list[dict[str, str]] = []

    async def request(self, method, path, *, headers, body=None):
        self.headers_seen.append(dict(headers))
        headers["authorization"] = "Bearer attacker-controlled"
        return AdminHTTPResponse(status=200, body={})


class SyncTransport:
    def __init__(self, response: AdminHTTPResponse) -> None:
        self.response = response
        self.thread_id: int | None = None

    def request(self, method, path, *, headers, body=None):
        self.thread_id = threading.get_ident()
        return self.response


class BlockingSyncTransport:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def request(self, method, path, *, headers, body=None):
        self.calls += 1
        self.started.set()
        self.release.wait(timeout=1)
        return AdminHTTPResponse(status=200, body={"calls": self.calls})


class StubbornAsyncTransport:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def request(self, method, path, *, headers, body=None):
        self.calls += 1
        self.started.set()
        if self.release.is_set():
            return AdminHTTPResponse(status=200, body={"calls": self.calls})
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await self.release.wait()
            return AdminHTTPResponse(status=200, body={"calls": self.calls})


@pytest.mark.asyncio
async def test_admin_client_authenticates_and_restricts_paths() -> None:
    transport = FakeTransport(AdminHTTPResponse(status=200, body={"status": "healthy"}))
    client = AdminClient(transport, token="secret-token")
    assert await client.inspect("health") == {"status": "healthy"}
    assert transport.calls[0][0:2] == ("GET", "/admin/health")
    assert transport.calls[0][2]["authorization"] == "Bearer secret-token"
    with pytest.raises(ValueError):
        await client.inspect("arbitrary")


@pytest.mark.asyncio
async def test_admin_client_exposes_safe_remote_errors() -> None:
    transport = FakeTransport(AdminHTTPResponse(status=403, body={"code": "security.forbidden"}))
    client = AdminClient(transport, token="token")
    with pytest.raises(AdminClientError, match="security.forbidden") as error:
        await client.restart_task("worker")
    assert error.value.status == 403
    with pytest.raises(ValueError):
        await client.service_action("bad/name", "restart")


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["private secret\n", "x" * 128, 42, None])
async def test_admin_client_sanitizes_malformed_remote_error_codes(code: object) -> None:
    transport = FakeTransport(AdminHTTPResponse(status=502, body={"code": code}))
    client = AdminClient(transport, token="token")
    with pytest.raises(AdminClientError, match="admin.remote-failure") as error:
        await client.inspect("health")
    assert error.value.code == "admin.remote-failure"


def test_admin_client_error_validates_public_contract() -> None:
    with pytest.raises(ValueError):
        AdminClientError(99, "admin.failure")
    with pytest.raises(ValueError):
        AdminClientError(399, "admin.failure")
    with pytest.raises(ValueError):
        AdminClientError(500, "unsafe code")


def test_admin_http_response_detaches_and_freezes_nested_body() -> None:
    original = {"nested": {"status": "healthy"}}
    response = AdminHTTPResponse(status=200, body=original)

    original["nested"]["status"] = "changed"
    assert response.body["nested"]["status"] == "healthy"
    with pytest.raises(TypeError):
        response.body["nested"]["status"] = "changed"  # type: ignore[index]


def test_admin_http_response_rejects_unbounded_or_unsafe_body_keys() -> None:
    with pytest.raises(ValueError):
        AdminHTTPResponse(status=101, body={})
    with pytest.raises(ValueError):
        AdminHTTPResponse(status=200, body={"unsafe\nkey": True})
    with pytest.raises(ValueError):
        AdminHTTPResponse(status=200, body={str(index): index for index in range(2_049)})
    with pytest.raises(ValueError):
        AdminHTTPResponse(status=200, body={1: "coerced-key"})  # type: ignore[dict-item]


@pytest.mark.asyncio
async def test_admin_client_detaches_headers_for_each_transport_call() -> None:
    transport = MutatingTransport()
    client = AdminClient(transport, token="stable-token")
    await client.inspect("health")
    await client.inspect("state")
    assert [headers["authorization"] for headers in transport.headers_seen] == [
        "Bearer stable-token",
        "Bearer stable-token",
    ]


@pytest.mark.asyncio
async def test_admin_client_runs_sync_transport_off_loop() -> None:
    transport = SyncTransport(AdminHTTPResponse(status=200, body={}))
    client = AdminClient(transport, token="token")
    assert await client.inspect("health") == {}
    assert transport.thread_id != threading.get_ident()
    with pytest.raises(ValueError):
        AdminClient(transport, token="token", max_sync_workers=0)
    with pytest.raises(ValueError):
        AdminClient(transport, token="token", timeout=float("nan"))


@pytest.mark.asyncio
async def test_admin_client_bounds_stalled_sync_workers_and_slot_waiters() -> None:
    transport = BlockingSyncTransport()
    client = AdminClient(transport, token="token", timeout=0.01, max_sync_workers=1)

    with pytest.raises(TimeoutError):
        await client.inspect("health")
    assert transport.started.wait(timeout=0.1)

    # The first timed-out call still owns the only worker slot, so this call times out while
    # waiting instead of starting another permanently blocked thread.
    with pytest.raises(TimeoutError):
        await client.inspect("health")
    assert transport.calls == 1

    transport.release.set()
    for _ in range(100):
        await asyncio.sleep(0.01)
        if client._sync_slots._value == 1:  # noqa: SLF001 - verify late slot release.
            break
    assert client._sync_slots._value == 1  # noqa: SLF001 - verify late slot release.


@pytest.mark.asyncio
async def test_admin_client_detaches_stubborn_async_operations_per_path() -> None:
    transport = StubbornAsyncTransport()
    client = AdminClient(transport, token="token", timeout=0.01)

    with pytest.raises(TimeoutError):
        await client.inspect("health")
    assert transport.started.is_set()

    with pytest.raises(TimeoutError):
        await client.inspect("health")
    assert transport.calls == 1

    transport.release.set()
    for _ in range(100):
        await asyncio.sleep(0.001)
        if not client._detached_async:  # noqa: SLF001 - wait for test-owned late work.
            break
    assert not client._detached_async  # noqa: SLF001 - verify late operation retirement.
    assert await client.inspect("health") == {"calls": 2}


@pytest.mark.asyncio
async def test_admin_client_bounds_distinct_stalled_async_operations() -> None:
    transport = StubbornAsyncTransport()
    client = AdminClient(
        transport,
        token="token",
        timeout=0.01,
        max_async_operations=1,
    )

    with pytest.raises(TimeoutError):
        await client.inspect("health")
    with pytest.raises(TimeoutError):
        await client.inspect("state")
    assert transport.calls == 1

    transport.release.set()
    for _ in range(100):
        await asyncio.sleep(0.001)
        if not client._detached_async and client._async_slots._value == 1:  # noqa: SLF001
            break
    assert not client._detached_async  # noqa: SLF001 - wait for test-owned late work.
    assert client._async_slots._value == 1  # noqa: SLF001 - verify late slot release.


@pytest.mark.asyncio
async def test_admin_client_bounds_awaitables_returned_by_sync_transport() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def stalled() -> AdminHTTPResponse:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            return AdminHTTPResponse(status=200, body={})

    class SyncAwaitableTransport:
        calls = 0

        def request(self, method, path, *, headers, body=None):
            self.calls += 1
            return stalled()

    transport = SyncAwaitableTransport()
    client = AdminClient(
        transport,
        token="token",
        timeout=0.01,
        max_sync_workers=2,
        max_async_operations=1,
    )

    with pytest.raises(TimeoutError):
        await client.inspect("health")
    await started.wait()
    with pytest.raises(TimeoutError):
        await client.inspect("state")
    assert transport.calls == 2

    release.set()
    for _ in range(100):
        await asyncio.sleep(0.001)
        if not client._detached_async and client._async_slots._value == 1:  # noqa: SLF001
            break
    assert not client._detached_async  # noqa: SLF001 - wait for test-owned late work.
    assert client._async_slots._value == 1  # noqa: SLF001 - verify late slot release.


@pytest.mark.asyncio
async def test_admin_client_recognizes_async_callable_transport() -> None:
    class CallableRequest:
        async def __call__(self, method, path, *, headers, body=None):
            return AdminHTTPResponse(status=200, body={"path": path})

    class CallableTransport:
        request = CallableRequest()

    client = AdminClient(CallableTransport(), token="token")
    assert await client.inspect("health") == {"path": "/admin/health"}


def test_admin_client_rejects_malformed_security_and_transport_inputs() -> None:
    response = AdminHTTPResponse(status=200, body={})
    transport = FakeTransport(response)
    with pytest.raises(ValueError):
        AdminClient(transport, token=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AdminClient(transport, token="x" * 16_385)
    with pytest.raises(ValueError):
        AdminClient(transport, token="token\x00")
    with pytest.raises(ValueError):
        AdminClient(transport, token="token", timeout="1")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AdminClient(transport, token="token", max_sync_workers="1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="1,000,000"):
        AdminClient(transport, token="token", max_sync_workers=1_000_001)
    with pytest.raises(ValueError, match="max_async_operations"):
        AdminClient(transport, token="token", max_async_operations=0)
    with pytest.raises(ValueError, match="1,000,000"):
        AdminClient(transport, token="token", max_async_operations=1_000_001)
    with pytest.raises(TypeError):
        AdminClient(object(), token="token")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        asyncio.run(AdminClient(transport, token="token").service_action("../secret", "restart"))
    with pytest.raises(ValueError):
        asyncio.run(AdminClient(transport, token="token").inspect([]))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        asyncio.run(AdminClient(transport, token="token").service_action("worker", []))  # type: ignore[arg-type]
