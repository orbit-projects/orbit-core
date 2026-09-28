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
"""Opt-in real-process validation for the supported Gunicorn/Uvicorn topology."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread
from typing import cast
from urllib.request import urlopen

import pytest

pytestmark = [
    pytest.mark.hosting,
    pytest.mark.skipif(
        os.environ.get("ORBIT_RUN_HOSTING_TESTS") != "1",
        reason="Set ORBIT_RUN_HOSTING_TESTS=1 to run real worker-process hosting tests.",
    ),
]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_http(port: int, process: subprocess.Popen[str], timeout: float = 15) -> bytes:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(f"Gunicorn exited with {process.returncode}: {output}")
        try:
            with urlopen(f"http://127.0.0.1:{port}/", timeout=1) as response:
                return cast(bytes, response.read())
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    raise AssertionError(f"Gunicorn did not become ready: {last_error}")


def _request_http(port: int, path: str = "/") -> bytes:
    """Fetch the smoke-test route and close the response before returning its body."""
    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
        return cast(bytes, response.read())


def _request_burst(port: int, count: int = 64) -> list[bytes]:
    """Exercise bounded concurrent request admission across the worker pool."""
    with ThreadPoolExecutor(max_workers=min(16, count)) as executor:
        return list(executor.map(lambda _: _request_http(port), range(count)))


def _request_soak(port: int, duration: float = 30.0) -> int:
    """Issue successful requests for one bounded interval and return the request count."""
    deadline = time.monotonic() + duration
    count = 0
    while time.monotonic() < deadline:
        if _request_http(port) != b"ok":
            raise AssertionError("Soak request returned an unexpected body.")
        count += 1
    return count


def _request_with_stalled_body(port: int) -> bytes:
    """Send one body frame, pause past Core's deadline, and capture the HTTP response."""
    request = (
        b"POST /echo HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 2\r\nConnection: close\r\n\r\na"
    )
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.sendall(request)
        time.sleep(0.35)
        return _read_http_response(connection)


def _request_with_conflicting_content_length(port: int) -> bytes:
    """Send conflicting HTTP framing and capture the host's bounded rejection."""
    request = (
        b"POST /echo HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Length: 1\r\n"
        b"Content-Length: 2\r\n"
        b"Connection: close\r\n\r\n"
        b"a"
    )
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.sendall(request)
        return _read_http_response(connection)


def _request_with_oversized_header(port: int) -> bytes:
    """Send a header beyond Core's default budget and capture bounded host rejection."""
    request = (
        b"GET / HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"X-Oversized: " + b"x" * (70 * 1024) + b"\r\nConnection: close\r\n\r\n"
    )
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.sendall(request)
        return _read_http_response(connection)


def _read_http_response(connection: socket.socket) -> bytes:
    """Read one bounded response using its declared content length."""
    connection.settimeout(5)
    chunks: list[bytes] = []
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            chunk = connection.recv(64 * 1024)
        except ConnectionResetError:
            # Uvicorn may reject an oversized header in its HTTP parser before an ASGI
            # response exists. A host-level reset is therefore an explicit rejection,
            # provided the caller verifies that the worker remains healthy afterward.
            return b"".join(chunks)
        if not chunk:
            break
        chunks.append(chunk)
        response = b"".join(chunks)
        separator = response.find(b"\r\n\r\n")
        if separator >= 0:
            header_block = response[:separator].lower()
            marker = b"content-length:"
            length_start = header_block.find(marker)
            if length_start >= 0:
                length_start += len(marker)
                length_end = header_block.find(b"\r\n", length_start)
                if length_end >= 0:
                    length = int(header_block[length_start:length_end].strip())
                    if len(response) - separator - 4 >= length:
                        break
        if sum(map(len, chunks)) > 1024 * 1024:
            raise AssertionError("Host response exceeded the test safety limit.")
    return b"".join(chunks)


def _request_through_forwarding_proxy(
    upstream_port: int, forwarded_for: str = "203.0.113.4"
) -> bytes:
    """Forward one request through a test proxy with one controlled client identity."""
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    proxy_port = int(listener.getsockname()[1])
    failures: list[BaseException] = []

    def forward() -> None:
        """Add one trusted proxy field and relay the request to the Uvicorn socket."""
        try:
            client, _ = listener.accept()
            with (
                client,
                socket.create_connection(("127.0.0.1", upstream_port), timeout=5) as upstream,
            ):
                client.settimeout(5)
                request = bytearray()
                while b"\r\n\r\n" not in request:
                    chunk = client.recv(64 * 1024)
                    if not chunk:
                        raise AssertionError("Proxy client closed before sending headers.")
                    request.extend(chunk)
                    if len(request) > 64 * 1024:
                        raise AssertionError("Proxy request headers exceeded the test limit.")
                marker = request.find(b"\r\n\r\n")
                forwarded = (
                    bytes(request[:marker])
                    + f"\r\nX-Forwarded-For: {forwarded_for}\r\nConnection: close\r\n\r\n".encode()
                )
                upstream.sendall(forwarded)
                while True:
                    chunk = upstream.recv(64 * 1024)
                    if not chunk:
                        break
                    client.sendall(chunk)
        except BaseException as exc:
            failures.append(exc)

    thread = Thread(target=forward, name="orbit-test-proxy", daemon=True)
    thread.start()
    try:
        request = b"GET /identity HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
        with socket.create_connection(("127.0.0.1", proxy_port), timeout=5) as client:
            client.sendall(request)
            response = _read_http_response(client)
    finally:
        listener.close()
        thread.join(timeout=5)
    if thread.is_alive():
        raise AssertionError("Test proxy did not finish forwarding the request.")
    if failures:
        raise AssertionError("Test proxy failed.") from failures[0]
    return response


def _wait_for_markers(directory: Path, prefix: str, count: int, timeout: float = 5) -> list[Path]:
    """Wait briefly for every worker marker to become visible on disk."""
    deadline = time.monotonic() + timeout
    markers: list[Path] = []
    while time.monotonic() < deadline:
        markers = sorted(directory.glob(f"{prefix}*"))
        if len(markers) >= count:
            return markers
        time.sleep(0.05)
    return markers


def _marker_pids(directory: Path, prefix: str) -> set[int]:
    """Return process IDs encoded by lifecycle marker names."""
    return {
        int(marker.name.removeprefix(prefix))
        for marker in directory.glob(f"{prefix}*")
        if marker.name.removeprefix(prefix).isdigit()
    }


def _write_host_app(
    tmp_path: Path, *, request_timeout: float = 30.0, trust_forwarded: bool = False
) -> tuple[Path, Path, Path]:
    """Create one lifecycle-instrumented Runtime module for process-host tests."""
    module = tmp_path / "host_app.py"
    started = tmp_path / "service-started"
    stopped = tmp_path / "service-stopped"
    in_flight = tmp_path / "request-in-flight"
    started.mkdir()
    stopped.mkdir()
    in_flight.mkdir()
    module.write_text(
        "import asyncio\n"
        "import os\n"
        "from pathlib import Path\n"
        "from orbit import Application, ApplicationConfig, Service, ServiceDescriptor\n"
        "from orbit.asgi import Response\n"
        "from orbit.runtime import Runtime\n"
        "class Managed(Service):\n"
        "    descriptor = ServiceDescriptor(name='managed')\n"
        "    async def start(self):\n"
        f"        marker = Path({str(started)!r}) / f'started-{{os.getpid()}}'\n"
        "        marker.write_text('started', encoding='utf-8')\n"
        "    async def stop(self):\n"
        f"        marker = Path({str(stopped)!r}) / f'stopped-{{os.getpid()}}'\n"
        "        marker.write_text('stopped', encoding='utf-8')\n"
        "application = Application(\n"
        f"    ApplicationConfig(name='host-smoke', request_timeout={request_timeout!r},\n"
        f"        trust_forwarded_headers={trust_forwarded!r},\n"
        f"        trusted_proxies={('127.0.0.1/32',) if trust_forwarded else ()!r})\n"
        ")\n"
        "application.register(Managed())\n"
        "@application.router.route('/', name='root')\n"
        "async def root(request):\n"
        "    return Response.text('ok')\n"
        "@application.router.route('/slow', name='slow')\n"
        "async def slow(request):\n"
        f"    marker = Path({str(in_flight)!r}) / f'request-{{os.getpid()}}'\n"
        "    marker.write_text('started', encoding='utf-8')\n"
        "    await asyncio.sleep(0.5)\n"
        "    return Response.text('slow')\n"
        "@application.router.route('/echo', method='POST', name='echo')\n"
        "async def echo(request):\n"
        "    return Response(\n"
        "        body=request.body, headers={'content-type': 'application/octet-stream'}\n"
        "    )\n"
        "@application.router.route('/identity', name='identity')\n"
        "async def identity(request):\n"
        "    return Response.text(request.client_host or 'none')\n"
        "runtime = Runtime(application)\n",
        encoding="utf-8",
    )
    return started, stopped, in_flight


def test_two_uvicorn_workers_serve_reload_and_terminate(tmp_path: Path) -> None:
    """Exercise repeated load, worker replacement, draining, and per-worker cleanup."""
    started, stopped, in_flight = _write_host_app(tmp_path, trust_forwarded=True)
    port = _free_port()
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")}
    orbit_command = str(Path(sys.executable).with_name("orbit"))
    process = subprocess.Popen(
        [
            orbit_command,
            "serve",
            "host_app:runtime",
            "--server",
            "gunicorn",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "2",
            "--worker-timeout",
            "10",
            "--graceful-timeout",
            "5",
        ],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert len(_wait_for_markers(started, "started-", 2)) == 2
        initial_pids = _marker_pids(started, "started-")
        assert _wait_for_http(port, process) == b"ok"
        proxied = _request_through_forwarding_proxy(port)
        assert proxied.endswith(b"203.0.113.4")
        malformed_proxy = _request_through_forwarding_proxy(port, "not-an-ip")
        assert malformed_proxy.endswith(b"127.0.0.1")
        oversized = _request_with_oversized_header(port)
        assert oversized.startswith((b"HTTP/1.1 400", b"HTTP/1.1 431"))
        for _ in range(4):
            assert _request_burst(port) == [b"ok"] * 64
        process.send_signal(signal.SIGHUP)
        assert len(_wait_for_markers(started, "started-", 4)) >= 4
        assert _wait_for_http(port, process) == b"ok"
        for _ in range(4):
            assert _request_burst(port) == [b"ok"] * 64
        replacement_pids = _marker_pids(started, "started-") - initial_pids
        assert len(replacement_pids) >= 2
        os.kill(next(iter(replacement_pids)), signal.SIGKILL)
        assert len(_wait_for_markers(started, "started-", 5)) >= 5
        assert _wait_for_http(port, process) == b"ok"
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(_request_http, port, "/slow")
            assert len(_wait_for_markers(in_flight, "request-", 1)) == 1
            process.terminate()
            assert pending.result(timeout=10) == b"slow"
        assert process.wait(timeout=10) == 0
        markers = _wait_for_markers(stopped, "stopped-", 4)
        assert len(markers) >= 4
        assert all(marker.read_text(encoding="utf-8") == "stopped" for marker in markers)
    finally:
        if process.poll() is None:
            process.kill()
        try:
            # ``wait()`` reaps the child but does not close PIPE handles.  Drain and
            # close both streams so strict warning runs do not report leaked files.
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


def test_uvicorn_development_process_serves_and_stops(tmp_path: Path) -> None:
    """Validate Orbit's direct-Uvicorn CLI path and ASGI lifespan cleanup."""
    # Keep the deadline tight while allowing the CLI-backed process to absorb the
    # intentionally heavy concurrent smoke traffic without turning the test flaky.
    started, stopped, _ = _write_host_app(tmp_path, request_timeout=0.2, trust_forwarded=True)
    port = _free_port()
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")}
    orbit_command = str(Path(sys.executable).with_name("orbit"))
    process = subprocess.Popen(
        [
            orbit_command,
            "serve",
            "host_app:runtime",
            "--server",
            "uvicorn",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=tmp_path,
        env=environment,
        # Uvicorn's default access log is expected during this sustained smoke test;
        # do not let an undrained stdout pipe apply backpressure to the server loop.
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert len(_wait_for_markers(started, "started-", 1)) == 1
        assert _wait_for_http(port, process) == b"ok"
        for _ in range(2):
            assert _request_burst(port, count=16) == [b"ok"] * 16
        for _ in range(8):
            assert _request_burst(port, count=32) == [b"ok"] * 32
        assert _request_soak(port) >= 20
        stalled = _request_with_stalled_body(port)
        assert stalled.startswith(b"HTTP/1.1 504")
        assert b"request.timeout" in stalled
        malformed = _request_with_conflicting_content_length(port)
        assert malformed.startswith(b"HTTP/1.1 400")
        oversized = _request_with_oversized_header(port)
        assert not oversized or oversized.startswith((b"HTTP/1.1 400", b"HTTP/1.1 431"))
        proxied = _request_through_forwarding_proxy(port)
        assert proxied.endswith(b"203.0.113.4")
        malformed_proxy = _request_through_forwarding_proxy(port, "not-an-ip")
        assert malformed_proxy.endswith(b"127.0.0.1")
        # Uvicorn's development CLI is stopped interactively with SIGINT; managed
        # production SIGTERM behavior is covered by the Gunicorn test above.
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=10) == 0
        assert len(_wait_for_markers(stopped, "stopped-", 1)) == 1
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
